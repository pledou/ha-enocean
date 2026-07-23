"""Representation of an EnOcean device."""

import inspect
import json
from typing import Any, cast

from enocean.protocol.constants import RORG
from enocean.protocol.packet import MSCPacket, Packet, RadioPacket
from jinja2 import Template

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect, dispatcher_send
from homeassistant.helpers.entity import Entity

from .const import (
    DATA_ENOCEAN,
    DOMAIN,
    ENOCEAN_DONGLE,
    LOGGER,
    SIGNAL_RECEIVE_MESSAGE,
    SIGNAL_SEND_MESSAGE,
)
from .types import EEPEntityDef


class EnOceanEntity(Entity):
    """Parent class for all entities associated with the EnOcean component."""

    def __init__(
        self,
        dev_id: list[int],
        data_field: str,
        attr_name: str | None = None,
        dev_name: str | None = None,
        dev_class: str | None = None,
        fields: EEPEntityDef | None = None,
    ) -> None:
        """Initialize the device."""
        self.dev_id = dev_id
        # Store device display name
        self._device_name = dev_name or f"EnOcean {format_device_id_hex(self.dev_id)}"
        # Enable has_entity_name to compose display name from device + entity name
        # Use attr_name (description) for human-readable display, fallback to data_field
        self._attr_has_entity_name = True
        self._attr_name = attr_name or data_field
        # Use data_field for unique_id to ensure registry stability
        self._attr_unique_id = f"{format_device_id_hex_underscore(self.dev_id)}-{data_field.lower().replace(' ', '_')}"
        # Store device display name separately and expose via device_info
        self._data_field = data_field
        # Use standard attribute for device/entity class when provided
        if dev_class is not None:
            self._attr_device_class = dev_class

        # Initialize command template to None (can be overridden by EEPEntityDef)
        self._command_template: str | None = None

        # Apply all properties from EEPEntityDef when provided
        if fields is not None and isinstance(fields, EEPEntityDef):
            if fields.icon:
                self._attr_icon = fields.icon
            if fields.entity_category:
                self._attr_entity_category = fields.entity_category
            if fields.command_template:
                self._command_template = fields.command_template

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, format_device_id_hex_underscore(self.dev_id))},
            name=getattr(self, "_device_name", None),
        )

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_RECEIVE_MESSAGE, self._message_received_callback
            )
        )

    @callback
    def _message_received_callback(self, packet):
        """Handle incoming packets.

        Note: Packet validation is handled by the dongle before dispatch.
        Only valid packets reach this callback.
        """
        # Skip teach-in packets (RORG 0xD4) - they don't contain sensor data
        if hasattr(packet, "rorg") and packet.rorg == 0xD4:
            return

        # Compare packet sender with device id
        if packet.sender == self.dev_id:
            self.value_changed(packet)

    @callback
    def value_changed(self, packet):
        """Update the internal state of the device when a packet arrives."""

    def send_command(self, data, optional, packet_type):
        """Send a command via the EnOcean dongle."""

        packet = Packet(packet_type, data=data, optional=optional)

        LOGGER.debug(
            "Created packet for %s: type=0x%02x, data_length=%d, optional_length=%d, packet=%s",
            format_device_id_hex(self.dev_id),
            packet_type,
            len(data) if data else 0,
            len(optional) if optional else 0,
            packet,
        )

        dispatcher_send(self.hass, SIGNAL_SEND_MESSAGE, packet)

    def _send_message(
        self,
        command_template: str | None = None,
        template_vars: dict[str, Any] | None = None,
        rorg: int | None = None,
        func: int | None = None,
        type_: int | None = None,
    ) -> None:
        """Send a message using command_template with Jinja2 rendering.

        Args:
            command_template: Jinja2 template string for command payload
            template_vars: Variables to pass to template (e.g., {'value': 50})
            rorg: RORG byte for the packet
            func: FUNC byte for the packet
            type_: TYPE byte for the packet
        """
        if not command_template:
            LOGGER.warning("No command_template provided for %s", self._attr_unique_id)
            return

        try:
            # Render the Jinja2 template
            template = Template(command_template)
            rendered = template.render(template_vars or {})

            LOGGER.debug(
                "Rendered command_template for %s: %s",
                format_device_id_hex(self.dev_id),
                rendered,
            )

            # Parse JSON result
            try:
                command_data = json.loads(rendered)
            except json.JSONDecodeError as err:
                LOGGER.error(
                    "Failed to parse command_template output as JSON for %s: %s",
                    self._attr_unique_id,
                    err,
                )
                return

            LOGGER.debug(
                "Parsed command data for %s: %s",
                format_device_id_hex(self.dev_id),
                command_data,
            )

            # Check if this is an MSC packet (VentilAirSec)
            # MSC packets are identified by the presence of MSC field or by rorg=0xD1079
            is_msc = "MSC" in command_data or (rorg is not None and rorg == 0xD1079)

            if is_msc:
                # Build MSC packet using MSCPacket constructor
                # VentilAirSec uses manufacturer 0x079
                manufacturer = 0x079

                # Extract command from 'command' (lowercase) or 'CMD' field
                cmd = command_data.get("command") or command_data.get("CMD")
                if cmd is None:
                    LOGGER.warning(
                        "No command field in MSC command_template output for %s",
                        self._attr_unique_id,
                    )
                    return

                # Get sender ID from dongle
                enocean_data = self.hass.data.get(DATA_ENOCEAN, {})
                dongle = enocean_data.get(ENOCEAN_DONGLE)
                if dongle is None:
                    LOGGER.warning(
                        "Cannot create MSC packet for %s: dongle unavailable",
                        self._attr_unique_id,
                    )
                    return
                sender = dongle.base_id

                # Prepare kwargs with all fields except MSC, command, send
                kwargs = {
                    k: v
                    for k, v in command_data.items()
                    if k not in ("MSC", "command", "CMD", "send")
                }

                LOGGER.debug(
                    "Creating MSC packet for %s: manufacturer=0x%03x, cmd=%s, sender=%s (type: %s), kwargs=%s",
                    format_device_id_hex(self.dev_id),
                    manufacturer,
                    cmd,
                    sender,
                    type(sender).__name__,
                    kwargs,
                )

                # Create MSC packet using constructor
                packet = MSCPacket(
                    manufacturer=manufacturer,
                    command=int(cmd),
                    destination=self.dev_id,
                    sender=sender,
                    **kwargs,
                )
                LOGGER.info(
                    "Sending MSC command to %s: manufacturer=0x%03x, cmd=%s, data=%s (hex: %s)",
                    format_device_id_hex(self.dev_id),
                    manufacturer,
                    cmd,
                    packet.data,
                    "".join(f"{b:02x}" for b in packet.data),
                )
                # Send using dispatcher
                dispatcher_send(self.hass, SIGNAL_SEND_MESSAGE, packet)
                LOGGER.info(
                    "MSC command sent successfully to %s: CMD=%s",
                    format_device_id_hex(self.dev_id),
                    cmd,
                )

            else:
                # Non-MSC packet handling - use RadioPacket.create for proper EEP encoding
                # This handles VLD (D2) and other profiles that need proper field encoding
                
                # Get sender ID from dongle
                enocean_data = self.hass.data.get(DATA_ENOCEAN, {})
                dongle = enocean_data.get(ENOCEAN_DONGLE)
                if dongle is None:
                    LOGGER.warning(
                        "Cannot create packet for %s: dongle unavailable",
                        self._attr_unique_id,
                    )
                    return

                # Determine RORG from rorg parameter or infer from data
                packet_rorg = None
                if rorg is not None:
                    if rorg == 0xD2:
                        packet_rorg = RORG.VLD
                    elif rorg == 0xA5:
                        packet_rorg = RORG.BS4
                    elif rorg == 0xF6:
                        packet_rorg = RORG.RPS
                    elif rorg == 0xD5:
                        packet_rorg = RORG.BS1
                    else:
                        LOGGER.warning(
                            "Unknown RORG 0x%02x for %s, using VLD",
                            rorg,
                            self._attr_unique_id,
                        )
                        packet_rorg = RORG.VLD
                else:
                    # Default to VLD if not specified
                    packet_rorg = RORG.VLD

                # Prepare kwargs for RadioPacket.create()
                # Filter out non-field keys
                packet_kwargs = {
                    k: int(v) if not isinstance(v, int) else v
                    for k, v in command_data.items()
                    if k not in ("send",)
                }

                LOGGER.debug(
                    "Creating RadioPacket for %s: rorg=%s, func=0x%02x, type=0x%02x, kwargs=%s",
                    format_device_id_hex(self.dev_id),
                    packet_rorg,
                    func or 0,
                    type_ or 0,
                    packet_kwargs,
                )

                try:
                    # Create packet using RadioPacket.create with EEP encoding
                    packet = RadioPacket.create(
                        rorg=packet_rorg,
                        rorg_func=func or 0,
                        rorg_type=type_ or 0,
                        destination=self.dev_id,
                        sender=dongle.base_id,
                        **packet_kwargs,
                    )

                    LOGGER.info(
                        "Sending RadioPacket to %s: rorg=%s, func=0x%02x, type=0x%02x, data=%s (hex: %s)",
                        format_device_id_hex(self.dev_id),
                        packet_rorg,
                        func or 0,
                        type_ or 0,
                        packet.data,
                        "".join(f"{b:02x}" for b in packet.data),
                    )

                    # Send using dispatcher
                    dispatcher_send(self.hass, SIGNAL_SEND_MESSAGE, packet)

                    LOGGER.info(
                        "RadioPacket sent successfully to %s",
                        format_device_id_hex(self.dev_id),
                    )

                except Exception as err:
                    LOGGER.error(
                        "Failed to create RadioPacket for %s: %s",
                        self._attr_unique_id,
                        err,
                    )
                    return

        except Exception as err:  # noqa: BLE001
            LOGGER.exception(
                "Error sending message for %s: %s", self._attr_unique_id, err
            )


def format_device_id_hex(dev_id: list[int]) -> str:
    """Return colon-separated hex string for a device id list.

    Example: [0x01,0x02,0x03,0x04] -> "01:02:03:04"
    """
    return ":".join(f"{byte:02x}" for byte in dev_id)


def format_device_id_hex_underscore(dev_id: list[int]) -> str:
    """Return underscore-separated hex string for a device id list.

    Example: [0x01,0x02,0x03,0x04] -> "01_02_03_04"
    """
    return "_".join(f"{byte:02x}" for byte in dev_id)


class DynamicEnoceanEntity(EnOceanEntity):
    """Base class for dynamic EnOcean entities that use pre-parsed packet data.

    This class expects packets to be pre-parsed by the dongle's callback.
    Entities simply read from packet.parsed instead of doing their own parsing.
    """

    def __init__(
        self,
        dev_id: list[int],
        data_field: str,
        rorg: int,
        rorg_func: int,
        rorg_type: int,
        dev_name: str | None = None,
        dev_class: str | None = None,
        attr_name: str | None = None,
        fields: EEPEntityDef | None = None,
    ) -> None:
        """Initialize the dynamic EnOcean entity."""
        # Call EnOceanEntity initializer directly to avoid MRO issues
        # where super() would resolve to a concrete sensor's __init__
        # that requires additional positional arguments.
        EnOceanEntity.__init__(
            self,
            dev_id=dev_id,
            data_field=data_field,
            attr_name=attr_name,
            dev_name=dev_name,
            dev_class=dev_class,
            fields=fields,
        )

        # Store EEP profile info for reference (parsing is done by dongle)
        self._rorg = rorg
        self._rorg_func = rorg_func
        self._rorg_type = rorg_type
        self._fields = fields
        # Note: EEPEntityDef properties (icon, entity_category, command_template)
        # are applied in EnOceanEntity base class __init__

    def _get_parsed_value(self, packet, field_name: str):
        """Get a field value from the pre-parsed packet data.

        The packet should already be parsed by the dongle's callback.
        This method simply extracts the requested field from packet.parsed.

        Args:
            packet: EnOcean packet with parsed data
            field_name: Name of the field to extract

        Returns:
            Field value or None if not found or out of range
        """
        if not packet.parsed:
            LOGGER.debug(
                "Packet for %s has no parsed data - ensure device profile is registered",
                self._attr_unique_id,
            )
            return None

        try:
            # Handle nested dict structure (e.g., {"FIELD": {"raw_value": 123}})
            field_data = packet.parsed.get(field_name)
        except (KeyError, AttributeError, TypeError) as err:
            LOGGER.debug(
                "Failed to extract field %s for %s: %s",
                field_name,
                self._attr_unique_id,
                err,
            )
            return None
        else:
            if isinstance(field_data, dict):
                # Check if value is out of range and skip it
                if field_data.get("out_of_range", False):
                    LOGGER.warning(
                        "Ignoring out-of-range value for field %s on device %s (raw_value=%s)",
                        field_name,
                        format_device_id_hex(self.dev_id),
                        field_data.get("raw_value"),
                    )
                    return None
                # For numeric fields with scaling, prefer "value" (scaled)
                # For enums, "value" might be a string description, so use "raw_value"
                if "value" in field_data:
                    value = field_data["value"]
                    # If value is numeric (scaled field), use it
                    if isinstance(value, (int, float)):
                        return value
                    # If value is string (enum description), use raw_value instead
                    return field_data.get("raw_value", value)
                # Fallback to raw_value if no value field
                return field_data.get("raw_value")
            return field_data


async def async_create_entities_from_eep(
    hass: HomeAssistant,
    config_entry,
    device_id: list[int],
    entities_list: list[EEPEntityDef] | None,
    rorg: int,
    rorg_func: int,
    rorg_type: int,
    platform_type: str,
    entity_class,
    async_add_entities,
    entity_kwargs_factory=None,
    entity_class_factory=None,
) -> None:
    """Factory function to create entities from EEP definitions.

    This shared function handles the common logic for all entity platforms:
    - Filtering entities by type
    - Generating consistent unique IDs
    - Avoiding duplicates
    - Loading EEP fields
    - Creating and adding entities

    Args:
        hass: Home Assistant instance
        config_entry: Config entry
        device_id: 4-byte device ID list
        entities_list: List of EEPEntityDef objects
        rorg, func, type_: EEP profile identifiers
        platform_type: Entity platform ("sensor", "binary_sensor", etc.)
        entity_class: The default entity class to instantiate
        async_add_entities: Callback to add entities
        entity_kwargs_factory: Optional callable(ent) -> dict of extra kwargs
        entity_class_factory: Optional callable(ent) -> entity class. If provided,
            overrides entity_class for each entity based on its definition.
    """

    if not entities_list:
        LOGGER.debug(
            "No entities provided for platform %s, device %s",
            platform_type,
            format_device_id_hex(device_id),
        )
        return

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_device(
        identifiers={("enocean", format_device_id_hex_underscore(device_id))}
    )
    if not device_entry:
        LOGGER.warning(
            "Device not found in registry for %s when creating %s entities",
            format_device_id_hex(device_id),
            platform_type,
        )
        return
    if config_entry.entry_id not in device_entry.config_entries:
        LOGGER.warning(
            "Config entry %s not in device config entries for %s",
            config_entry.entry_id,
            format_device_id_hex(device_id),
        )
        return

    device_name = device_entry.name or f"enocean {format_device_id_hex(device_id)}"
    new_entities = []
    seen_unique_ids = set()  # Track unique IDs to prevent duplicates

    entities_filtered = 0
    for ent in entities_list:
        try:
            # Filter by entity type
            entity_type = getattr(ent, "entity_type", "sensor")
            # Handle EntityType enum by getting its value
            entity_type_str = (
                entity_type.value if hasattr(entity_type, "value") else entity_type
            )
            if entity_type_str != platform_type:
                entities_filtered += 1
                continue

            # Generate consistent entity ID
            unique_suffix = (
                (ent.data_field or ent.description or "entity")
                .lower()
                .replace(" ", "_")
            )
            device_hex_underscore = format_device_id_hex_underscore(device_id)
            unique_id = f"{device_hex_underscore}-{unique_suffix}"
            attr_name = ent.description or ent.data_field or "Entity"

            # Skip if we've already created an entity with this unique ID
            if unique_id in seen_unique_ids:
                continue
            seen_unique_ids.add(unique_id)

            fields_for_kwargs = ent

            # Build entity kwargs
            entity_kwargs: dict[str, Any] = {
                "data_field": ent.data_field,
                "device_class": ent.device_class,
                "rorg": rorg,
                "rorg_func": rorg_func,
                "rorg_type": rorg_type,
                "fields": fields_for_kwargs,
            }

            if attr_name:
                entity_kwargs["attr_name"] = attr_name

            # Add enum_options if available (for select entities)
            if hasattr(ent, "enum_options") and ent.enum_options:
                entity_kwargs["enum_options"] = cast(Any, ent.enum_options)

            # Add platform-specific kwargs
            if entity_kwargs_factory:
                extra_kwargs = entity_kwargs_factory(ent)
                if extra_kwargs:
                    entity_kwargs.update(extra_kwargs)

            entity_kwargs.setdefault("dev_name", device_name)

            # Inspect the target constructor and build positional args
            # for any required positional parameters to avoid passing the
            # same argument both positionally and via kwargs which can
            # raise a TypeError in some subclass __init__ implementations.

            # Select the appropriate entity class using factory if provided
            selected_entity_class = (
                entity_class_factory(ent) if entity_class_factory else entity_class
            )

            try:
                sig = inspect.signature(selected_entity_class.__init__)
                # parameter list excluding 'self'
                params = list(sig.parameters.values())[1:]
                param_names = [p.name for p in params]
            except (ValueError, TypeError):
                params = []
                param_names = []

            positional_args: list[int | str | EEPEntityDef | Any] = []
            # If the constructor expects a first parameter like 'dev_id',
            # pass the device_id positionally
            if param_names and param_names[0] in ("dev_id"):
                positional_args.append(device_id)

            # For required positional parameters after the first, consume
            # them from entity_kwargs and pass positionally to avoid
            # duplicate-assignment errors
            positional_args.extend(
                entity_kwargs.pop(p.name)
                for p in params[1:]
                if (
                    p.kind
                    in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    )
                    and p.default is inspect.Parameter.empty
                    and p.name in entity_kwargs
                )
            )

            # Filter kwargs to parameters the constructor actually accepts
            filtered_kwargs = {
                k: v
                for k, v in entity_kwargs.items()
                if (not param_names) or (k in param_names)
            }

            # Create the entity using constructed args/kwargs with selected class
            entity_obj = selected_entity_class(*positional_args, **filtered_kwargs)
            new_entities.append(entity_obj)

        except (FileNotFoundError, OSError, ValueError, TypeError) as err:
            LOGGER.exception(
                "Failed to create %s entity from EEP def: %s, error: %s",
                platform_type,
                ent,
                err,
            )

    if new_entities:
        async_add_entities(new_entities)
