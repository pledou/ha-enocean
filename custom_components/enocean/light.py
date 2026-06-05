"""Support for EnOcean light sources."""

from __future__ import annotations

import math
from typing import Any

import voluptuous as vol

from enocean.protocol.constants import RORG
from enocean.protocol.packet import RadioPacket
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    PLATFORM_SCHEMA as LIGHT_PLATFORM_SCHEMA,
    ColorMode,
    ConfigEntry,
    LightEntity,
)
from homeassistant.const import CONF_ID, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import DATA_ENOCEAN, ENOCEAN_DONGLE, LOGGER, SIGNAL_SEND_MESSAGE
from .entity import DynamicEnoceanEntity, EnOceanEntity, async_create_entities_from_eep
from .types import EEPEntityDef

CONF_SENDER_ID = "sender_id"

DEFAULT_NAME = "EnOcean Light"

PLATFORM_SCHEMA = LIGHT_PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_ID, default=[]): vol.All(cv.ensure_list, [vol.Coerce(int)]),
        vol.Required(CONF_SENDER_ID): vol.All(cv.ensure_list, [vol.Coerce(int)]),
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    }
)


def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the EnOcean light platform."""
    sender_id: list[int] = config[CONF_SENDER_ID]
    dev_name: str = config[CONF_NAME]
    dev_id: list[int] = config[CONF_ID]

    add_entities([EnOceanLight(sender_id, dev_id, dev_name)])


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the EnOcean light platform from config entry."""
    enocean_data = hass.data.get(DATA_ENOCEAN, {})
    
    # Register callback to add light entities discovered via EEP
    async def _add_lights_from_eep(
        device_id, entities_list, rorg, rorg_func, rorg_type
    ):
        """Add light entities for a discovered device from EEP profile."""

        def _kwargs_factory(ent):
            # Extract channel from entity
            # For YAML-defined entities, channel is stored in offset field
            if hasattr(ent, "offset") and ent.offset is not None:
                return {"channel": int(ent.offset)}
            # Fallback: try dict-style access for backwards compatibility
            if isinstance(ent, dict):
                config = ent.get("config", {})
                channel = config.get("channel")
                if channel is not None:
                    try:
                        return {"channel": int(channel)}
                    except (TypeError, ValueError):
                        pass
            return None

        await async_create_entities_from_eep(
            hass,
            config_entry,
            device_id,
            entities_list,
            rorg,
            rorg_func,
            rorg_type,
            platform_type="light",
            entity_class=DynamicEnOceanLight,
            async_add_entities=async_add_entities,
            entity_kwargs_factory=_kwargs_factory,
        )

    # Register the callback in the platform callbacks registry
    platform_callbacks = enocean_data.get("platform_callbacks", {})
    platform_callbacks["light"] = _add_lights_from_eep


class EnOceanLight(EnOceanEntity, LightEntity):
    """Representation of an EnOcean light source."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_brightness = 50
    _attr_is_on = False

    def __init__(
        self,
        sender_id: list[int],
        dev_id: list[int],
        dev_name: str,
        channel: int | None = None,
    ) -> None:
        """Initialize the EnOcean light source."""
        super().__init__(dev_id, data_field="brightness")
        self._sender_id = sender_id
        self._attr_name = dev_name
        self.channel = channel
        
        # D2 on/off relays don't support brightness, only A5 dimmers do
        if channel is not None:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_color_modes = {ColorMode.ONOFF}

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the light source on or sets a specific dimmer value."""
        # D2 VLD profiles (with channel) - simple on/off relay
        if self.channel is not None:
            enocean_data = self.hass.data.get(DATA_ENOCEAN, {})
            dongle = enocean_data.get(ENOCEAN_DONGLE)
            if not dongle:
                LOGGER.error("Cannot turn on light %s: dongle unavailable", self.dev_id)
                return
            
            # Use RadioPacket.create() for D2-01-* profiles
            packet = RadioPacket.create(
                rorg=RORG.VLD,
                rorg_func=0x01,
                rorg_type=0x01,
                destination=self.dev_id,
                sender=dongle.base_id,
                command=1,  # Actuator Set Output
                DV=0,  # Switch mode
                IO=self.channel & 0xFF,
                OV=100  # ON
            )
            dispatcher_send(self.hass, SIGNAL_SEND_MESSAGE, packet)
            LOGGER.debug(
                "Sent turn_on to %s channel %d",
                ":".join(f"{b:02x}" for b in self.dev_id),
                self.channel
            )
        else:
            # A5 (4BS) dimmer profiles - legacy support
            if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
                self._attr_brightness = brightness

            bval = math.floor(self._attr_brightness / 256.0 * 100.0)
            if bval == 0:
                bval = 1
            command = [0xA5, 0x02, bval, 0x01, 0x09]
            command.extend(self._sender_id)
            command.extend([0x00])
            self.send_command(command, [], 0x01)
        
        self._attr_is_on = True

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the light source off."""
        # D2 VLD profiles (with channel) - simple on/off relay
        if self.channel is not None:
            enocean_data = self.hass.data.get(DATA_ENOCEAN, {})
            dongle = enocean_data.get(ENOCEAN_DONGLE)
            if not dongle:
                LOGGER.error("Cannot turn off light %s: dongle unavailable", self.dev_id)
                return
            
            # Use RadioPacket.create() for D2-01-* profiles
            packet = RadioPacket.create(
                rorg=RORG.VLD,
                rorg_func=0x01,
                rorg_type=0x01,
                destination=self.dev_id,
                sender=dongle.base_id,
                command=1,  # Actuator Set Output
                DV=0,  # Switch mode
                IO=self.channel & 0xFF,
                OV=0  # OFF
            )
            dispatcher_send(self.hass, SIGNAL_SEND_MESSAGE, packet)
            LOGGER.debug(
                "Sent turn_off to %s channel %d",
                ":".join(f"{b:02x}" for b in self.dev_id),
                self.channel
            )
        else:
            # A5 (4BS) dimmer profiles - legacy support
            command = [0xA5, 0x02, 0x00, 0x01, 0x09]
            command.extend(self._sender_id)
            command.extend([0x00])
            self.send_command(command, [], 0x01)
        
        self._attr_is_on = False

    @callback
    def value_changed(self, packet):
        """Update the internal state of this device.

        Dimmer devices like Eltako FUD61 send telegram in different RORGs.
        We only care about the 4BS (0xA5).
        """
        if packet.data[0] == 0xA5 and packet.data[1] == 0x02:
            val = packet.data[2]
            self._attr_brightness = math.floor(val / 100.0 * 256.0)
            self._attr_is_on = bool(val != 0)
            self.schedule_update_ha_state()


class DynamicEnOceanLight(DynamicEnoceanEntity, EnOceanLight):
    """Dynamic light that uses EEP parser/fields when available."""

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
        """Initialize the dynamic EnOcean light device."""
        # DynamicEnoceanEntity requires proper initialization
        DynamicEnoceanEntity.__init__(
            self,
            dev_id=dev_id,
            rorg=rorg,
            rorg_func=rorg_func,
            rorg_type=rorg_type,
            data_field=data_field,
            attr_name=attr_name,
            dev_name=dev_name,
            dev_class=device_class,
            fields=fields,
        )
        # Store channel for VLD devices
        self.channel = channel
        self._sender_id = []  # Not used for VLD devices, kept for compatibility
        
        # Store command templates for ON/OFF commands if available
        self._command_template_on = None
        self._command_template_off = None
        if fields is not None and isinstance(fields, EEPEntityDef):
            # Check for command templates in fields config
            if hasattr(fields, "command_template_on"):
                self._command_template_on = fields.command_template_on
            if hasattr(fields, "command_template_off"):
                self._command_template_off = fields.command_template_off
        
        # D2 on/off relays don't support brightness
        if channel is not None:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_color_modes = {ColorMode.ONOFF}
        else:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def turn_on(self, **kwargs: Any) -> None:
        """Turn the light source on."""
        # If command template is available, use it
        if hasattr(self, "_command_template_on") and self._command_template_on:
            self._send_message(
                command_template=self._command_template_on,
                template_vars={},  # No variables needed - channel is hardcoded in template
                rorg=self._rorg,
                func=self._rorg_func,
                type_=self._rorg_type,
            )
            self._attr_is_on = True
        else:
            # Fallback to parent class implementation
            super().turn_on(**kwargs)

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the light source off."""
        # If command template is available, use it
        if hasattr(self, "_command_template_off") and self._command_template_off:
            self._send_message(
                command_template=self._command_template_off,
                template_vars={},  # No variables needed - channel is hardcoded in template
                rorg=self._rorg,
                func=self._rorg_func,
                type_=self._rorg_type,
            )
            self._attr_is_on = False
        else:
            # Fallback to parent class implementation
            super().turn_off(**kwargs)
