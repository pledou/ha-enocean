"""Support for EnOcean switches."""

from __future__ import annotations

from typing import Any

from enocean.protocol.constants import RORG
from enocean.protocol.packet import RadioPacket
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ID, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DATA_ENOCEAN, DOMAIN, ENOCEAN_DONGLE, LOGGER
from .dongle import SIGNAL_LEARNING_MODE_CHANGED
from .entity import DynamicEnoceanEntity, EnOceanEntity, async_create_entities_from_eep
from .types import EEPEntityDef

CONF_CHANNEL = "channel"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EnOcean switch entities."""
    enocean_data = hass.data.get(DATA_ENOCEAN, {})
    dongle = enocean_data.get(ENOCEAN_DONGLE)

    if not dongle:
        return

    entities = [
        EnOceanLearnSwitch(dongle),
    ]

    async_add_entities(entities)

    # Register callback to add switch entities discovered via EEP using shared factory
    async def _add_switches_from_eep(
        device_id, entities_list, rorg, rorg_func, rorg_type
    ):
        """Add switch entities for a discovered device from EEP profile."""

        def _kwargs_factory(ent):
            # Use channel/index from offset if available as fallback
            channel = (
                ent.get("offset")
                if isinstance(ent, dict)
                else getattr(ent, "offset", None)
            )
            if channel is None:
                return None
            try:
                return {"channel": int(channel)}
            except (TypeError, ValueError):
                return None

        await async_create_entities_from_eep(
            hass,
            config_entry,
            device_id,
            entities_list,
            rorg,
            rorg_func,
            rorg_type,
            platform_type="switch",
            entity_class=DynamicEnOceanSwitch,
            async_add_entities=async_add_entities,
            entity_kwargs_factory=_kwargs_factory,
        )

    # Register the callback in the platform callbacks registry
    platform_callbacks = enocean_data.get("platform_callbacks", {})
    platform_callbacks["switch"] = _add_switches_from_eep


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the EnOcean switch platform."""
    channel: int = config[CONF_CHANNEL]
    dev_id: list[int] = config[CONF_ID]
    dev_name: str = config[CONF_NAME]
    async_add_entities(
        [
            EnOceanSwitch(
                dev_id,
                data_field=dev_name,
                attr_name=dev_name,
                dev_name=None,
                channel=channel,
            )
        ]
    )


class EnOceanSwitch(EnOceanEntity, SwitchEntity):
    """Representation of an EnOcean switch device."""

    _attr_is_on = False

    def __init__(
        self,
        dev_id: list[int],
        data_field: str,
        attr_name: str | None = None,
        dev_name: str | None = None,
        channel: int | None = None,
        fields: EEPEntityDef | None = None,
    ) -> None:
        """Initialize the EnOcean switch device."""
        # Use channel as part of the data_field for unique ID if provided
        # This ensures multi-channel devices have distinct identifiers.
        effective_field = data_field
        if channel is not None and "channel" not in data_field.lower():
            effective_field = f"{data_field}_channel_{channel}"

        EnOceanEntity.__init__(
            self,
            dev_id,
            data_field=effective_field,
            attr_name=attr_name or data_field,
            dev_name=dev_name,
            dev_class=None,
            fields=fields,
        )
        self._light = None
        self.channel = channel
        self._attr_name = attr_name or dev_name or data_field

    def turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        # Get dongle base_id for sender
        enocean_data = self.hass.data.get(DATA_ENOCEAN, {})
        dongle = enocean_data.get(ENOCEAN_DONGLE)
        if not dongle:
            LOGGER.error("Cannot turn on switch %s: dongle unavailable", self.dev_id)
            return
        
        # Use RadioPacket.create() for proper VLD packet construction
        # This works for D2-01-* profiles (D2-01-01, D2-01-12, etc.)
        packet = RadioPacket.create(
            rorg=RORG.VLD,
            rorg_func=0x01,
            rorg_type=0x01,  # Use 0x01 as default; profiles share same command structure
            destination=self.dev_id,
            sender=dongle.base_id,
            command=1,  # Actuator Set Output
            DV=0,  # Switch to new output value (no dimming)
            IO=(self.channel or 0) & 0xFF,  # I/O channel
            OV=100  # Output value 100% = ON
        )
        
        from homeassistant.helpers.dispatcher import dispatcher_send
        from .const import SIGNAL_SEND_MESSAGE
        dispatcher_send(self.hass, SIGNAL_SEND_MESSAGE, packet)
        
        LOGGER.debug(
            "Sent turn_on command to %s channel %d",
            ":".join(f"{b:02x}" for b in self.dev_id),
            self.channel or 0
        )
        
        self._attr_is_on = True

    def turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        # Get dongle base_id for sender
        enocean_data = self.hass.data.get(DATA_ENOCEAN, {})
        dongle = enocean_data.get(ENOCEAN_DONGLE)
        if not dongle:
            LOGGER.error("Cannot turn off switch %s: dongle unavailable", self.dev_id)
            return
        
        # Use RadioPacket.create() for proper VLD packet construction
        # This works for D2-01-* profiles (D2-01-01, D2-01-12, etc.)
        packet = RadioPacket.create(
            rorg=RORG.VLD,
            rorg_func=0x01,
            rorg_type=0x01,  # Use 0x01 as default; profiles share same command structure
            destination=self.dev_id,
            sender=dongle.base_id,
            command=1,  # Actuator Set Output
            DV=0,  # Switch to new output value (no dimming)
            IO=(self.channel or 0) & 0xFF,  # I/O channel
            OV=0  # Output value 0% = OFF
        )
        
        from homeassistant.helpers.dispatcher import dispatcher_send
        from .const import SIGNAL_SEND_MESSAGE
        dispatcher_send(self.hass, SIGNAL_SEND_MESSAGE, packet)
        
        LOGGER.debug(
            "Sent turn_off command to %s channel %d",
            ":".join(f"{b:02x}" for b in self.dev_id),
            self.channel or 0
        )
        
        self._attr_is_on = False

    @callback
    def value_changed(self, packet):
        """Update the internal state of the switch."""
        if packet.data[0] == 0xA5:
            # power meter telegram, turn on if > 10 watts
            packet.parse_eep(0x12, 0x01)
            if packet.parsed["DT"]["raw_value"] == 1:
                raw_val = packet.parsed["MR"]["raw_value"]
                divisor = packet.parsed["DIV"]["raw_value"]
                watts = raw_val / (10**divisor)
                if watts > 1:
                    self._attr_is_on = True
                    self.schedule_update_ha_state()
        elif packet.data[0] == 0xD2:
            # actuator status telegram
            packet.parse_eep(0x01, 0x01)
            if packet.parsed["CMD"]["raw_value"] == 4:
                channel = packet.parsed["IO"]["raw_value"]
                output = packet.parsed["OV"]["raw_value"]
                if channel == self.channel:
                    self._attr_is_on = output > 0
                    self.schedule_update_ha_state()


class DynamicEnOceanSwitch(DynamicEnoceanEntity, EnOceanSwitch):
    """Dynamic switch that uses EEP parser/fields when available."""

    def __init__(
        self,
        dev_id: list[int],
        rorg: int,
        rorg_func: int,
        rorg_type: int,
        data_field: str,
        attr_name: str | None = None,
        dev_name: str | None = None,
        channel: int | None = None,
        device_class: str | None = None,
        fields: EEPEntityDef | None = None,
    ) -> None:
        """Initialize the dynamic EnOcean switch device."""
        # Initialize dynamic base (parser/fields) then EnOceanSwitch
        DynamicEnoceanEntity.__init__(
            self,
            dev_id,
            data_field=data_field,
            rorg=rorg,
            rorg_func=rorg_func,
            rorg_type=rorg_type,
            dev_name=dev_name,
            dev_class=device_class,
            fields=fields,
        )
        EnOceanSwitch.__init__(
            self,
            dev_id,
            data_field=data_field,
            attr_name=attr_name,
            dev_name=dev_name,
            channel=channel,
            fields=fields,
        )

        # Apply switch-specific device_class from EEPEntityDef if available
        if (
            fields is not None
            and isinstance(fields, EEPEntityDef)
            and fields.device_class
        ):
            self._attr_device_class = fields.device_class  # type: ignore[assignment]

        # Store command template for sending commands when available
        if fields is not None and isinstance(fields, EEPEntityDef):
            if fields.command_template is not None:
                self._command_template = fields.command_template

    def turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        if self._command_template:
            # Use command template to send turn_on command
            self._send_message(
                self._command_template,
                {
                    "value": 1,
                    "state": "on",
                    "device_id": self.dev_id,
                    "channel": self.channel or 0,
                },
                self._rorg,
                self._rorg_func,
                self._rorg_type,
            )
            self._attr_is_on = True
            self.async_write_ha_state()
        else:
            # Fallback to base implementation
            super().turn_on(**kwargs)

    def turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        if self._command_template:
            # Use command template to send turn_off command
            self._send_message(
                self._command_template,
                {
                    "value": 0,
                    "state": "off",
                    "device_id": self.dev_id,
                    "channel": self.channel or 0,
                },
                self._rorg,
                self._rorg_func,
                self._rorg_type,
            )
            self._attr_is_on = False
            self.async_write_ha_state()
        else:
            # Fallback to base implementation
            super().turn_off(**kwargs)

    @callback
    def value_changed(self, packet):
        """Prefer parsed values via parser/fields, fallback to base implementation."""
        if not packet.data or len(packet.data) < 2:
            return

        # Packet should already be parsed by dongle callback
        if not packet.parsed or not self._fields:
            # Fallback to base implementation for non-dynamic packets
            super().value_changed(packet)
            return

        try:
            # Try to extract channel and output fields commonly used
            ch = (
                packet.parsed.get("IO")
                or packet.parsed.get("CH")
                or packet.parsed.get("IO_NUM")
            )
            
            # Try to get the value from the field matching this switch's data_field
            # This handles LC (Local Control), OV (Output Value), etc.
            out = packet.parsed.get(self._data_field)
            if out is None:
                # Fallback to common output field names
                out = (
                    packet.parsed.get("OV")
                    or packet.parsed.get("OUT")
                    or packet.parsed.get("OUTPUT")
                )
            
            if isinstance(ch, dict):
                ch = ch.get("raw_value") or ch.get("value")
            if isinstance(out, dict):
                out = out.get("raw_value") or out.get("value")
            if ch is not None and out is not None:
                try:
                    if int(ch) == int(self.channel):
                        self._attr_is_on = int(out) > 0
                        self.schedule_update_ha_state()
                        return
                except (ValueError, TypeError):
                    pass
        except (ValueError, TypeError, KeyError) as err:
            LOGGER.debug(
                "Parser failed for dynamic switch %s: %s", self._attr_unique_id, err
            )

        # Fallback to original logic
        super().value_changed(packet)


class EnOceanLearnSwitch(SwitchEntity):
    """Representation of an EnOcean learn mode switch."""

    _attr_has_entity_name = True
    _attr_is_on = False

    def __init__(self, dongle) -> None:
        """Initialize the learn mode switch."""
        self._dongle = dongle
        self._attr_unique_id = f"{dongle.identifier}-learn"
        self._attr_name = "Learning mode"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the dongle."""
        return {
            "identifiers": {(DOMAIN, self._dongle.identifier)},
            "name": f"EnOcean Dongle ({self._dongle.identifier})",
            "manufacturer": "EnOcean",
        }

    async def async_added_to_hass(self) -> None:
        """Register callback for learning mode changes."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_LEARNING_MODE_CHANGED,
                self._learning_mode_changed_callback,
            )
        )
        # Set initial state
        self._attr_is_on = self._dongle.learning_mode
        self.async_write_ha_state()

    async def _learning_mode_changed_callback(self, data: dict) -> None:
        """Handle learning mode changes."""
        self._attr_is_on = data.get("enabled", False)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on learning mode."""
        await self._dongle.async_start_learning()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off learning mode."""
        await self._dongle.async_stop_learning()
