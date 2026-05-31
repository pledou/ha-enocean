"""Support for EnOcean buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_ENOCEAN, ENOCEAN_DONGLE, LOGGER
from .entity import DynamicEnoceanEntity, EnOceanEntity, async_create_entities_from_eep
from .types import EEPEntityDef

EVENT_BUTTON_PRESSED = "button_pressed"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EnOcean button entities."""
    enocean_data = hass.data.get(DATA_ENOCEAN, {})
    dongle = enocean_data.get(ENOCEAN_DONGLE)

    if not dongle:
        return

    # Register callback to add button entities discovered via EEP
    async def _add_buttons_from_eep(
        device_id, entities_list, rorg, rorg_func, rorg_type
    ):
        def _button_class_factory(ent: EEPEntityDef | None):
            """Select button class based on entity definition."""
            # Check if this is a CommandTemplateButton (has command_template)
            if (
                isinstance(ent, EEPEntityDef)
                and hasattr(ent, "command_template")
                and ent.command_template
            ):
                return CommandTemplateButton
            # Otherwise use regular DynamicEnOceanButton
            return DynamicEnOceanButton

        def _kwargs_factory(ent: EEPEntityDef | None):
            # Check if this is a CommandTemplateButton
            if (
                isinstance(ent, EEPEntityDef)
                and hasattr(ent, "command_template")
                and ent.command_template
            ):
                # CommandTemplateButton only needs button_name
                button_name = ent.description or "Command Button"
                return {"button_name": button_name}

            # For regular buttons, extract channel/offset
            if isinstance(ent, EEPEntityDef):
                channel = ent.offset
                description = ent.description or (
                    f"Button {channel}" if channel is not None else None
                )
            else:
                channel = getattr(ent, "offset", None)
                description = getattr(ent, "description", None) or (
                    f"Button {channel}" if channel is not None else None
                )

            if channel is None:
                return None

            try:
                normalized_channel = int(channel)
                return {
                    "channel": normalized_channel,
                    "button_name": description,
                }
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
            platform_type="button",
            entity_class=DynamicEnOceanButton,
            async_add_entities=async_add_entities,
            entity_kwargs_factory=_kwargs_factory,
            entity_class_factory=_button_class_factory,
        )

    # Register the callback in the platform callbacks registry
    platform_callbacks = enocean_data.get("platform_callbacks", {})
    platform_callbacks["button"] = _add_buttons_from_eep


class EnOceanButton(EnOceanEntity, ButtonEntity):
    """Representation of an EnOcean button device."""

    def __init__(
        self,
        dev_id: list[int],
        dev_name: str,
        channel: int,
        button_name: str,
        fields: EEPEntityDef | None = None,
    ) -> None:
        """Initialize the EnOcean button device."""
        super().__init__(
            dev_id,
            data_field=f"{button_name}_{channel}",
            attr_name=button_name,
            dev_name=dev_name,
            fields=fields,
        )
        self.channel = channel
        self._button_name = button_name
        self._attr_name = f"{dev_name} {button_name}"

    async def async_press(self) -> None:
        """Press the button."""
        optional = [0x03]
        optional.extend(self.dev_id)
        optional.extend([0xFF, 0x00])
        self.send_command(
            data=[0xF6, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
            optional=optional,
            packet_type=0x01,
        )


class DynamicEnOceanButton(DynamicEnoceanEntity, EnOceanButton):
    """Representation of a dynamic EnOcean button device."""

    def __init__(
        self,
        dev_id: list[int],
        dev_name: str,
        channel: int,
        button_name: str,
        rorg: int,
        rorg_func: int,
        rorg_type: int,
        fields: EEPEntityDef | None = None,
    ) -> None:
        """Initialize the dynamic EnOcean button device."""
        super().__init__(
            dev_id=dev_id,
            dev_name=dev_name,
            data_field=f"{button_name}_{channel}",
            rorg=rorg,
            rorg_func=rorg_func,
            rorg_type=rorg_type,
            attr_name=button_name,
            fields=fields,
        )
        # Initialize button-specific attributes
        self.channel = channel
        self._button_name = button_name
        self._attr_name = f"{dev_name} {button_name}"

    @callback
    def value_changed(self, packet) -> None:
        """Handle value changes for button entity.
        
        Note: F6 RPS profiles are now handled by event platform.
        This method is kept for potential non-F6 button implementations.
        """
        pass


class CommandTemplateButton(DynamicEnoceanEntity, ButtonEntity):
    """EnOcean button that sends commands via command_template.

    Unlike DynamicEnOceanButton which requires a channel for F6 packets,
    this button uses command_template (like MSC packets) for more complex
    commands. This is useful for devices like VentilAirSec that use MSC
    protocol instead of simple channel-based buttons.
    """

    def __init__(
        self,
        dev_id: list[int],
        dev_name: str,
        rorg: int,
        rorg_func: int,
        rorg_type: int,
        fields: EEPEntityDef | None = None,
        button_name: str | None = None,
    ) -> None:
        """Initialize the command template button."""
        # Initialize DynamicEnoceanEntity without channel requirement
        DynamicEnoceanEntity.__init__(
            self,
            dev_id=dev_id,
            data_field=button_name or "command_button",
            rorg=rorg,
            rorg_func=rorg_func,
            rorg_type=rorg_type,
            dev_name=dev_name,
            fields=fields,
        )

        # Set button name
        self._attr_name = button_name or "Command Button"

        # Store command_template from fields if available
        self._command_template = None
        if fields and hasattr(fields, "command_template"):
            self._command_template = fields.command_template

    async def async_press(self) -> None:
        """Press the button by sending command via command_template."""
        if not self._command_template:
            LOGGER.warning(
                "Button %s has no command_template configured",
                self._attr_name,
            )
            return

        # Send the command using _send_message which handles command_template
        self._send_message(
            command_template=self._command_template,
            rorg=self.rorg if hasattr(self, "rorg") else None,
            func=self.rorg_func if hasattr(self, "rorg_func") else None,
            type_=self.rorg_type if hasattr(self, "rorg_type") else None,
        )
