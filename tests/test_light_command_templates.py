"""Tests for DynamicEnOceanLight command template functionality."""

from unittest.mock import Mock, patch

import pytest

from custom_components.enocean.const import DATA_ENOCEAN, ENOCEAN_DONGLE
from custom_components.enocean.light import DynamicEnOceanLight
from custom_components.enocean.types import EEPEntityDef, EntityType


@pytest.fixture
def mock_dongle():
    """Create a mock dongle with base_id."""
    dongle = Mock()
    dongle.base_id = [0xFF, 0xFF, 0xFF, 0xFF]
    return dongle


@pytest.fixture
def mock_hass(mock_dongle):
    """Create a mock Home Assistant instance."""
    hass = Mock()
    hass.data = {DATA_ENOCEAN: {ENOCEAN_DONGLE: mock_dongle}}
    return hass


def test_dynamic_light_uses_command_template_on():
    """Test that DynamicEnOceanLight uses command_template_on when available."""
    dev_id = [0x05, 0x87, 0x5D, 0xB4]
    
    # Create EEPEntityDef with command templates
    fields = EEPEntityDef(
        description="Test Light",
        rorg=0xD2,
        rorg_func=0x01,
        rorg_type=0x12,
        data_field="channel_0",
        entity_type=EntityType.LIGHT,
        command_template_on='{"CMD": 1, "DV": 0, "IO": {{channel}}, "OV": 100}',
        command_template_off='{"CMD": 1, "DV": 0, "IO": {{channel}}, "OV": 0}',
    )
    
    # Create light entity
    light = DynamicEnOceanLight(
        dev_id=dev_id,
        rorg=0xD2,
        rorg_func=0x01,
        rorg_type=0x12,
        data_field="channel_0",
        attr_name="Channel 0",
        dev_name="Test Device",
        channel=0,
        fields=fields,
    )
    
    # Verify command templates are stored
    assert light._command_template_on == '{"CMD": 1, "DV": 0, "IO": {{channel}}, "OV": 100}'
    assert light._command_template_off == '{"CMD": 1, "DV": 0, "IO": {{channel}}, "OV": 0}'


def test_dynamic_light_turn_on_calls_send_message(mock_hass):
    """Test that turn_on calls _send_message with correct parameters."""
    dev_id = [0x05, 0x87, 0x5D, 0xB4]
    
    fields = EEPEntityDef(
        description="Test Light",
        rorg=0xD2,
        rorg_func=0x01,
        rorg_type=0x12,
        data_field="channel_0",
        entity_type=EntityType.LIGHT,
        command_template_on='{"CMD": 1, "DV": 0, "IO": {{channel}}, "OV": 100}',
        command_template_off='{"CMD": 1, "DV": 0, "IO": {{channel}}, "OV": 0}',
    )
    
    light = DynamicEnOceanLight(
        dev_id=dev_id,
        rorg=0xD2,
        rorg_func=0x01,
        rorg_type=0x12,
        data_field="channel_0",
        attr_name="Channel 0",
        dev_name="Test Device",
        channel=0,
        fields=fields,
    )
    
    # Inject hass instance
    light.hass = mock_hass
    light.dev_id = dev_id
    
    # Mock _send_message
    with patch.object(light, '_send_message') as mock_send:
        light.turn_on()
        
        # Verify _send_message was called with correct parameters
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs['command_template'] == '{"CMD": 1, "DV": 0, "IO": {{channel}}, "OV": 100}'
        assert call_kwargs['template_vars'] == {'channel': 0}
        assert call_kwargs['rorg'] == 0xD2
        assert call_kwargs['func'] == 0x01
        assert call_kwargs['type_'] == 0x12
        
    # Verify state changed
    assert light._attr_is_on is True


def test_dynamic_light_turn_off_calls_send_message(mock_hass):
    """Test that turn_off calls _send_message with correct parameters."""
    dev_id = [0x05, 0x87, 0x5D, 0xB4]
    
    fields = EEPEntityDef(
        description="Test Light",
        rorg=0xD2,
        rorg_func=0x01,
        rorg_type=0x12,
        data_field="channel_0",
        entity_type=EntityType.LIGHT,
        command_template_on='{"CMD": 1, "DV": 0, "IO": {{channel}}, "OV": 100}',
        command_template_off='{"CMD": 1, "DV": 0, "IO": {{channel}}, "OV": 0}',
    )
    
    light = DynamicEnOceanLight(
        dev_id=dev_id,
        rorg=0xD2,
        rorg_func=0x01,
        rorg_type=0x12,
        data_field="channel_0",
        attr_name="Channel 0",
        dev_name="Test Device",
        channel=1,
        fields=fields,
    )
    
    light.hass = mock_hass
    light.dev_id = dev_id
    
    with patch.object(light, '_send_message') as mock_send:
        light.turn_off()
        
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs['command_template'] == '{"CMD": 1, "DV": 0, "IO": {{channel}}, "OV": 0}'
        assert call_kwargs['template_vars'] == {'channel': 1}
        assert call_kwargs['rorg'] == 0xD2
        assert call_kwargs['func'] == 0x01
        assert call_kwargs['type_'] == 0x12
        
    assert light._attr_is_on is False


def test_dynamic_light_fallback_without_command_template(mock_hass):
    """Test that light falls back to parent implementation when no command template."""
    dev_id = [0x05, 0x87, 0x5D, 0xB4]
    
    # Fields without command templates
    fields = EEPEntityDef(
        description="Test Light",
        rorg=0xD2,
        rorg_func=0x01,
        rorg_type=0x12,
        data_field="channel_0",
        entity_type=EntityType.LIGHT,
    )
    
    light = DynamicEnOceanLight(
        dev_id=dev_id,
        rorg=0xD2,
        rorg_func=0x01,
        rorg_type=0x12,
        data_field="channel_0",
        attr_name="Channel 0",
        dev_name="Test Device",
        channel=0,
        fields=fields,
    )
    
    light.hass = mock_hass
    light.dev_id = dev_id
    
    # Mock _send_message and parent turn_on
    with patch.object(light, '_send_message') as mock_send, \
         patch('custom_components.enocean.light.EnOceanLight.turn_on') as mock_parent:
        light.turn_on()
        
        # _send_message should NOT be called (no command template)
        mock_send.assert_not_called()
        
        # Parent turn_on should be called
        mock_parent.assert_called_once()
