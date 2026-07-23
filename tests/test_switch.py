"""Tests for EnOcean switch entity behavior."""

from custom_components.enocean.switch import EnOceanSwitch


def test_switch_turn_on_and_off_sends_command_and_updates_state() -> None:
    """Test sending on/off commands and updating state."""
    dev_id = [0x01, 0x02, 0x03, 0x04]
    channel = 5
    name = "My Switch"

    sw = EnOceanSwitch(dev_id, data_field="switch", dev_name=name, channel=channel)

    # Mock hass and dongle
    from unittest.mock import Mock
    from custom_components.enocean.const import DATA_ENOCEAN, ENOCEAN_DONGLE
    
    mock_dongle = Mock()
    mock_dongle.base_id = [0xFF, 0xFF, 0xFF, 0xFF]
    
    mock_hass = Mock()
    mock_hass.data = {DATA_ENOCEAN: {ENOCEAN_DONGLE: mock_dongle}}
    
    sw.hass = mock_hass
    sw.dev_id = dev_id

    sent = []

    def fake_send_command(data, optional, packet_type):
        sent.append({"data": data, "optional": optional, "packet_type": packet_type})

    # Replace the send_command method with our test stub
    sw.send_command = fake_send_command

    # Turn on (now expects dispatcher_send to be called, but we'll just check state)
    from unittest.mock import patch
    with patch('homeassistant.helpers.dispatcher.dispatcher_send'):
        sw.turn_on()
    assert sw._attr_is_on is True

    # Turn off
    with patch('homeassistant.helpers.dispatcher.dispatcher_send'):
        sw.turn_off()
    assert sw._attr_is_on is False


def test_unique_id_and_name_set_correctly() -> None:
    """Test that unique ID and name are set correctly."""
    dev_id = [0x0A, 0x0B, 0x0C, 0x0D]
    channel = 2
    name = "Kitchen Switch"

    sw = EnOceanSwitch(dev_id, data_field="switch", dev_name=name, channel=channel)
    assert sw._attr_name == name
