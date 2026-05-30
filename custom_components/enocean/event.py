"""Support for EnOcean event entities."""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_ENOCEAN
from .entity import DynamicEnoceanEntity, async_create_entities_from_eep
from .types import EEPEntityDef


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EnOcean event entities."""
    enocean_data = hass.data.get(DATA_ENOCEAN, {})

    async def _add_events_from_eep(
        device_id: list[int],
        entities_list: list[EEPEntityDef],
        rorg: int,
        rorg_func: int,
        rorg_type: int,
    ) -> None:
        def _kwargs_factory(ent: EEPEntityDef | None):
            if not isinstance(ent, EEPEntityDef):
                return None

            # Extract event types from enum_options
            event_types = ent.enum_options if ent.enum_options else []
            if not event_types:
                return None

            try:
                return {
                    "event_name": ent.data_field or "event",
                    "event_types": event_types,
                    "enum_items": ent.enum_items or [],
                    "field_offset": ent.offset,
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
            platform_type="event",
            entity_class=DynamicEnOceanEvent,
            async_add_entities=async_add_entities,
            entity_kwargs_factory=_kwargs_factory,
        )

    platform_callbacks = enocean_data.get("platform_callbacks", {})
    platform_callbacks["event"] = _add_events_from_eep


class DynamicEnOceanEvent(DynamicEnoceanEntity, EventEntity):
    """Representation of a stateless EnOcean RPS event entity following EEP profiles."""

    def __init__(
        self,
        dev_id: list[int],
        dev_name: str,
        event_name: str,
        event_types: list[str],
        enum_items: list[dict],
        field_offset: int | None,
        rorg: int,
        rorg_func: int,
        rorg_type: int,
        fields: EEPEntityDef | None = None,
        attr_name: str | None = None,
    ) -> None:
        """Initialize the EnOcean event entity."""
        DynamicEnoceanEntity.__init__(
            self,
            dev_id=dev_id,
            data_field=event_name,
            rorg=rorg,
            rorg_func=rorg_func,
            rorg_type=rorg_type,
            dev_name=dev_name,
            fields=fields,
            attr_name=attr_name or event_name,
        )
        self._field_name = event_name
        self._field_offset = field_offset
        self._enum_items = enum_items
        # Build value-to-description mapping
        self._value_map = {
            int(item["value"]): item["description"]
            for item in enum_items
            if "value" in item and "description" in item
        }
        self._attr_name = f"{dev_name} {event_name}"
        self._attr_device_class = EventDeviceClass.BUTTON
        self._attr_event_types = event_types

    @callback
    def value_changed(self, packet) -> None:
        """Trigger an event when RPS field value changes from parsed packet."""
        if not hasattr(packet, "rorg") or packet.rorg != 0xF6:
            return

        # Get parsed field value from packet
        if not hasattr(packet, "parsed") or not packet.parsed:
            return

        field_data = packet.parsed.get(self._field_name)
        if field_data is None:
            return

        # Extract raw value
        if isinstance(field_data, dict):
            raw_value = field_data.get("raw_value", field_data.get("value"))
        else:
            raw_value = field_data

        if raw_value is None:
            return

        # Map value to event type description
        event_type = self._value_map.get(int(raw_value))
        if not event_type or event_type not in self._attr_event_types:
            return

        # Only trigger on press telegrams (EB field = 1)
        eb_data = packet.parsed.get("EB")
        if eb_data:
            eb_value = eb_data.get("raw_value", eb_data.get("value")) if isinstance(eb_data, dict) else eb_data
            if eb_value != 1:  # not pressed
                return

        event_attributes: dict[str, object] = {
            "id": self.dev_id,
            "field": self._field_name,
            "value": int(raw_value),
            "rorg": f"0x{self._rorg:02x}",
            "func": f"0x{self._rorg_func:02x}",
            "type": f"0x{self._rorg_type:02x}",
        }

        self._trigger_event(event_type, event_attributes)
        self.async_write_ha_state()
