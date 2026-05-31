"""Tests for EnOcean event platform."""

from unittest.mock import Mock, patch

import pytest
from enocean.protocol.constants import RORG

from custom_components.enocean.const import DATA_ENOCEAN, DOMAIN
from custom_components.enocean.event import DynamicEnOceanEvent, async_setup_entry
from custom_components.enocean.types import EEPEntityDef, EntityType

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.fixture
def mock_event_entity_def():
    """Create a mock EEPEntityDef for event entities."""
    return EEPEntityDef(
        description="Rocker 1",
        rorg=0xF6,
        rorg_func=0x02,
        rorg_type=0x01,
        entity_type=EntityType.EVENT,
        data_field="R1",
        offset=7,
        unit=None,
        enum_options=["Button AI pressed", "Button AO pressed", "Button BI pressed", "Button BO pressed"],
        enum_items=[
            {"value": 7, "description": "Button AI pressed"},
            {"value": 5, "description": "Button AO pressed"},
            {"value": 3, "description": "Button BI pressed"},
            {"value": 1, "description": "Button BO pressed"},
        ],
    )


async def test_async_setup_entry_registers_callback(hass: HomeAssistant) -> None:
    """Test that async_setup_entry registers the platform callback."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"device": "/dev/ttyUSB0"},
    )
    config_entry.add_to_hass(hass)

    # Initialize enocean data
    hass.data[DATA_ENOCEAN] = {"platform_callbacks": {}}

    async_add_entities = Mock()

    await async_setup_entry(hass, config_entry, async_add_entities)

    # Verify callback is registered
    assert "event" in hass.data[DATA_ENOCEAN]["platform_callbacks"]
    assert callable(hass.data[DATA_ENOCEAN]["platform_callbacks"]["event"])


async def test_add_events_from_eep_with_valid_entity(
    hass: HomeAssistant, mock_event_entity_def
) -> None:
    """Test _add_events_from_eep with valid event entity."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"device": "/dev/ttyUSB0"},
    )
    config_entry.add_to_hass(hass)

    hass.data[DATA_ENOCEAN] = {"platform_callbacks": {}}
    async_add_entities = Mock()

    await async_setup_entry(hass, config_entry, async_add_entities)

    # Get the registered callback
    add_events_callback = hass.data[DATA_ENOCEAN]["platform_callbacks"]["event"]

    device_id = [0x01, 0x23, 0x45, 0x67]
    entities_list = [mock_event_entity_def]

    # Call the callback
    with patch(
        "custom_components.enocean.event.async_create_entities_from_eep"
    ) as mock_create:
        await add_events_callback(device_id, entities_list, 0xF6, 0x02, 0x01)
        
        # Verify async_create_entities_from_eep was called
        assert mock_create.called
        call_args = mock_create.call_args
        assert call_args[0][2] == device_id  # device_id
        assert call_args[0][3] == entities_list  # entities_list
        assert call_args[1]["platform_type"] == "event"
        assert call_args[1]["entity_class"] == DynamicEnOceanEvent


async def test_add_events_from_eep_with_empty_enum_options(hass: HomeAssistant) -> None:
    """Test _add_events_from_eep skips entities with no enum_options."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"device": "/dev/ttyUSB0"},
    )
    config_entry.add_to_hass(hass)

    hass.data[DATA_ENOCEAN] = {"platform_callbacks": {}}
    async_add_entities = Mock()

    await async_setup_entry(hass, config_entry, async_add_entities)

    # Entity with no enum_options
    entity_def = EEPEntityDef(
        description="Rocker 1",
        rorg=0xF6,
        rorg_func=0x02,
        rorg_type=0x01,
        entity_type=EntityType.EVENT,
        data_field="R1",
        offset=7,
        unit=None,
        enum_options=[],  # Empty
        enum_items=[],
    )

    add_events_callback = hass.data[DATA_ENOCEAN]["platform_callbacks"]["event"]

    with patch(
        "custom_components.enocean.event.async_create_entities_from_eep"
    ) as mock_create:
        await add_events_callback([0x01, 0x02, 0x03, 0x04], [entity_def], 0xF6, 0x02, 0x01)
        
        # Should still be called (filtering happens in kwargs_factory)
        assert mock_create.called


def test_dynamic_event_initialization(mock_event_entity_def) -> None:
    """Test DynamicEnOceanEvent initialization."""
    event = DynamicEnOceanEvent(
        dev_id=[0x01, 0x23, 0x45, 0x67],
        dev_name="Test Button",
        event_name="R1",
        event_types=["Button AI pressed", "Button AO pressed"],
        enum_items=[
            {"value": 7, "description": "Button AI pressed"},
            {"value": 5, "description": "Button AO pressed"},
        ],
        field_offset=7,
        rorg=0xF6,
        rorg_func=0x02,
        rorg_type=0x01,
        fields=mock_event_entity_def,
    )

    assert event._field_name == "R1"
    assert event._field_offset == 7
    assert len(event._value_map) == 2
    assert event._value_map[7] == "Button AI pressed"
    assert event._value_map[5] == "Button AO pressed"
    assert event.device_class == "button"
    assert event.event_types == ["Button AI pressed", "Button AO pressed"]


def test_value_changed_valid_packet(mock_event_entity_def) -> None:
    """Test value_changed with valid RPS packet."""
    event = DynamicEnOceanEvent(
        dev_id=[0x01, 0x23, 0x45, 0x67],
        dev_name="Test Button",
        event_name="R1",
        event_types=["Button AI pressed", "Button AO pressed"],
        enum_items=[
            {"value": 7, "description": "Button AI pressed"},
            {"value": 5, "description": "Button AO pressed"},
        ],
        field_offset=7,
        rorg=0xF6,
        rorg_func=0x02,
        rorg_type=0x01,
    )

    packet = Mock()
    packet.rorg = 0xF6
    packet.parsed = {
        "R1": {"raw_value": 7, "value": 7},
        "EB": {"raw_value": 1, "value": 1},  # Pressed
    }

    with patch.object(event, "_trigger_event") as mock_trigger:
        with patch.object(event, "async_write_ha_state") as mock_write:
            event.value_changed(packet)

            # Verify event was triggered
            mock_trigger.assert_called_once()
            call_args = mock_trigger.call_args[0]
            assert call_args[0] == "Button AI pressed"
            assert call_args[1]["value"] == 7
            assert call_args[1]["field"] == "R1"
            mock_write.assert_called_once()


def test_value_changed_non_rps_packet() -> None:
    """Test value_changed ignores non-RPS packets."""
    event = DynamicEnOceanEvent(
        dev_id=[0x01, 0x23, 0x45, 0x67],
        dev_name="Test Button",
        event_name="R1",
        event_types=["Button AI pressed"],
        enum_items=[{"value": 7, "description": "Button AI pressed"}],
        field_offset=7,
        rorg=0xF6,
        rorg_func=0x02,
        rorg_type=0x01,
    )

    packet = Mock()
    packet.rorg = 0xA5  # Not RPS
    packet.parsed = {"R1": {"raw_value": 7}}

    with patch.object(event, "_trigger_event") as mock_trigger:
        event.value_changed(packet)
        mock_trigger.assert_not_called()


def test_value_changed_no_parsed_data() -> None:
    """Test value_changed with packet missing parsed data."""
    event = DynamicEnOceanEvent(
        dev_id=[0x01, 0x23, 0x45, 0x67],
        dev_name="Test Button",
        event_name="R1",
        event_types=["Button AI pressed"],
        enum_items=[{"value": 7, "description": "Button AI pressed"}],
        field_offset=7,
        rorg=0xF6,
        rorg_func=0x02,
        rorg_type=0x01,
    )

    packet = Mock()
    packet.rorg = 0xF6
    packet.parsed = None  # No parsed data

    with patch.object(event, "_trigger_event") as mock_trigger:
        event.value_changed(packet)
        mock_trigger.assert_not_called()


def test_value_changed_missing_field() -> None:
    """Test value_changed when field is missing from parsed data."""
    event = DynamicEnOceanEvent(
        dev_id=[0x01, 0x23, 0x45, 0x67],
        dev_name="Test Button",
        event_name="R1",
        event_types=["Button AI pressed"],
        enum_items=[{"value": 7, "description": "Button AI pressed"}],
        field_offset=7,
        rorg=0xF6,
        rorg_func=0x02,
        rorg_type=0x01,
    )

    packet = Mock()
    packet.rorg = 0xF6
    packet.parsed = {"R2": {"raw_value": 5}}  # Different field

    with patch.object(event, "_trigger_event") as mock_trigger:
        event.value_changed(packet)
        mock_trigger.assert_not_called()


def test_value_changed_eb_not_pressed() -> None:
    """Test value_changed filters out release telegrams (EB=0)."""
    event = DynamicEnOceanEvent(
        dev_id=[0x01, 0x23, 0x45, 0x67],
        dev_name="Test Button",
        event_name="R1",
        event_types=["Button AI pressed"],
        enum_items=[{"value": 7, "description": "Button AI pressed"}],
        field_offset=7,
        rorg=0xF6,
        rorg_func=0x02,
        rorg_type=0x01,
    )

    packet = Mock()
    packet.rorg = 0xF6
    packet.parsed = {
        "R1": {"raw_value": 7, "value": 7},
        "EB": {"raw_value": 0, "value": 0},  # Released
    }

    with patch.object(event, "_trigger_event") as mock_trigger:
        event.value_changed(packet)
        mock_trigger.assert_not_called()


def test_value_changed_r2_with_sa_invalid() -> None:
    """Test value_changed for R2 field filters when SA=0 (2nd action invalid)."""
    event = DynamicEnOceanEvent(
        dev_id=[0x01, 0x23, 0x45, 0x67],
        dev_name="Test Button",
        event_name="R2",
        event_types=["Button CI pressed"],
        enum_items=[{"value": 7, "description": "Button CI pressed"}],
        field_offset=3,
        rorg=0xF6,
        rorg_func=0x02,
        rorg_type=0x01,
    )

    packet = Mock()
    packet.rorg = 0xF6
    packet.parsed = {
        "R2": {"raw_value": 7, "value": 7},
        "EB": {"raw_value": 1, "value": 1},
        "SA": {"raw_value": 0, "value": 0},  # 2nd action not valid
    }

    with patch.object(event, "_trigger_event") as mock_trigger:
        event.value_changed(packet)
        mock_trigger.assert_not_called()


def test_value_changed_r2_with_sa_valid() -> None:
    """Test value_changed for R2 field triggers when SA=1 (2nd action valid)."""
    event = DynamicEnOceanEvent(
        dev_id=[0x01, 0x23, 0x45, 0x67],
        dev_name="Test Button",
        event_name="R2",
        event_types=["Button CI pressed"],
        enum_items=[{"value": 7, "description": "Button CI pressed"}],
        field_offset=3,
        rorg=0xF6,
        rorg_func=0x02,
        rorg_type=0x01,
    )

    packet = Mock()
    packet.rorg = 0xF6
    packet.parsed = {
        "R2": {"raw_value": 7, "value": 7},
        "EB": {"raw_value": 1, "value": 1},
        "SA": {"raw_value": 1, "value": 1},  # 2nd action valid
    }

    with patch.object(event, "_trigger_event") as mock_trigger:
        with patch.object(event, "async_write_ha_state"):
            event.value_changed(packet)
            mock_trigger.assert_called_once()


def test_value_changed_unknown_event_type() -> None:
    """Test value_changed ignores values not in event_types list."""
    event = DynamicEnOceanEvent(
        dev_id=[0x01, 0x23, 0x45, 0x67],
        dev_name="Test Button",
        event_name="R1",
        event_types=["Button AI pressed"],  # Only one type allowed
        enum_items=[
            {"value": 7, "description": "Button AI pressed"},
            {"value": 5, "description": "Button AO pressed"},  # Not in event_types
        ],
        field_offset=7,
        rorg=0xF6,
        rorg_func=0x02,
        rorg_type=0x01,
    )

    packet = Mock()
    packet.rorg = 0xF6
    packet.parsed = {
        "R1": {"raw_value": 5, "value": 5},  # Value not in allowed event_types
        "EB": {"raw_value": 1, "value": 1},
    }

    with patch.object(event, "_trigger_event") as mock_trigger:
        event.value_changed(packet)
        mock_trigger.assert_not_called()


def test_value_changed_with_non_dict_field_data() -> None:
    """Test value_changed handles non-dict field values."""
    event = DynamicEnOceanEvent(
        dev_id=[0x01, 0x23, 0x45, 0x67],
        dev_name="Test Button",
        event_name="R1",
        event_types=["Button AI pressed"],
        enum_items=[{"value": 7, "description": "Button AI pressed"}],
        field_offset=7,
        rorg=0xF6,
        rorg_func=0x02,
        rorg_type=0x01,
    )

    packet = Mock()
    packet.rorg = 0xF6
    packet.parsed = {
        "R1": 7,  # Direct value, not dict
        "EB": 1,
    }

    with patch.object(event, "_trigger_event") as mock_trigger:
        with patch.object(event, "async_write_ha_state"):
            event.value_changed(packet)
            mock_trigger.assert_called_once()


def test_value_changed_with_value_field_instead_of_raw_value() -> None:
    """Test value_changed uses 'value' when 'raw_value' is missing."""
    event = DynamicEnOceanEvent(
        dev_id=[0x01, 0x23, 0x45, 0x67],
        dev_name="Test Button",
        event_name="R1",
        event_types=["Button AI pressed"],
        enum_items=[{"value": 7, "description": "Button AI pressed"}],
        field_offset=7,
        rorg=0xF6,
        rorg_func=0x02,
        rorg_type=0x01,
    )

    packet = Mock()
    packet.rorg = 0xF6
    packet.parsed = {
        "R1": {"value": 7},  # Only 'value', no 'raw_value'
        "EB": {"value": 1},
    }

    with patch.object(event, "_trigger_event") as mock_trigger:
        with patch.object(event, "async_write_ha_state"):
            event.value_changed(packet)
            mock_trigger.assert_called_once()
            assert mock_trigger.call_args[0][1]["value"] == 7


def test_value_changed_with_none_raw_value() -> None:
    """Test value_changed returns early when raw_value is None."""
    event = DynamicEnOceanEvent(
        dev_id=[0x01, 0x23, 0x45, 0x67],
        dev_name="Test Button",
        event_name="R1",
        event_types=["Button AI pressed"],
        enum_items=[{"value": 7, "description": "Button AI pressed"}],
        field_offset=7,
        rorg=0xF6,
        rorg_func=0x02,
        rorg_type=0x01,
    )

    packet = Mock()
    packet.rorg = 0xF6
    packet.parsed = {
        "R1": {"raw_value": None, "value": None},  # Both None
        "EB": {"raw_value": 1, "value": 1},
    }

    with patch.object(event, "_trigger_event") as mock_trigger:
        event.value_changed(packet)
        mock_trigger.assert_not_called()
