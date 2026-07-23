"""Test D2-01-12 UTE teach-in and operational packet handling."""

from __future__ import annotations

from unittest.mock import Mock, patch

from enocean.protocol.constants import RORG
import pytest

from custom_components.enocean.const import CONF_DEVICE_PROFILES
from custom_components.enocean.dongle import EnOceanDongle, SIGNAL_DISCOVER_DEVICE, SIGNAL_RECEIVE_MESSAGE
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry

DOMAIN = "enocean"


@pytest.fixture
def mock_serial_communicator():
    """Mock the SerialCommunicator."""
    with patch(
        "custom_components.enocean.dongle.SerialCommunicator"
    ) as mock_comm:
        mock_instance = Mock()
        mock_instance.teach_in = False
        mock_instance.start = Mock()
        mock_instance.stop = Mock()
        mock_instance.base_id = [0xFF, 0x9C, 0x80, 0x80]
        mock_comm.return_value = mock_instance
        yield mock_instance


@pytest.mark.parametrize("expected_lingering_tasks", [True])
@pytest.mark.timeout(5)
async def test_d2_01_12_ute_teachin_then_operational_packet(
    hass: HomeAssistant, mock_serial_communicator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test D2-01-12 device using UTE teach-in followed by operational packets.
    
    Validates:
    1. UTE teach-in registers D2-01-12 profile
    2. Discovery is triggered with correct profile
    3. Subsequent operational D2 packet is processed (not ignored)
    4. Operational packet doesn't trigger duplicate discovery
    """
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: "/dev/ttyUSB0", CONF_DEVICE_PROFILES: {}},
        unique_id="test_dongle",
    )
    config_entry.add_to_hass(hass)
    dongle = EnOceanDongle(hass, "/dev/ttyUSB0", config_entry)
    dongle._communicator.teach_in = True

    dispatched: list[tuple[str, dict]] = []

    def _fake_dispatcher_send(_hass, signal, payload):
        dispatched.append((signal, payload))

    monkeypatch.setattr(
        "custom_components.enocean.dongle.dispatcher_send", _fake_dispatcher_send
    )

    # Mock RadioPacket and UTETeachInPacket classes
    class DummyRadioPacket:
        """Simple stand-in for enocean RadioPacket."""
        def parse_eep(self, rorg_func=None, rorg_type=None, direction=None, command=None):
            """Mock parse_eep - test already sets parsed dict."""
            pass

    class DummyUTETeachInPacket(DummyRadioPacket):
        """Simple stand-in for UTETeachInPacket."""

    monkeypatch.setattr("custom_components.enocean.dongle.RadioPacket", DummyRadioPacket)
    monkeypatch.setattr("custom_components.enocean.dongle.UTETeachInPacket", DummyUTETeachInPacket)

    # Step 1: Simulate UTE teach-in packet from D2-01-12 device
    ute_packet = DummyUTETeachInPacket()
    ute_packet.rorg = RORG.UTE
    ute_packet.sender = [0x05, 0x87, 0x5D, 0xB4]
    ute_packet.destination = [0xFF, 0xFF, 0xFF, 0xFF]  # Broadcast
    ute_packet.rorg_of_eep = RORG.VLD  # D2 = VLD = 0xD2
    ute_packet.rorg_manufacturer = 0x046  # Manufacturer code
    ute_packet.rorg_func = 0x01
    ute_packet.rorg_type = 0x12
    ute_packet.dBm = -82

    dongle.callback(ute_packet)
    await hass.async_block_till_done()

    # Verify profile was registered
    device_key = tuple(ute_packet.sender)
    assert device_key in dongle._device_profiles
    assert dongle._device_profiles[device_key]["rorg"] == int(RORG.VLD)
    assert dongle._device_profiles[device_key]["func"] == 0x01
    assert dongle._device_profiles[device_key]["type"] == 0x12

    # Verify discovery was triggered once
    discovery_signals = [s for s, _ in dispatched if s == SIGNAL_DISCOVER_DEVICE]
    assert len(discovery_signals) == 1
    
    signal, discovery_info = dispatched[0]
    assert signal == SIGNAL_DISCOVER_DEVICE
    assert discovery_info["device_id"] == ute_packet.sender
    assert discovery_info["eep_profile"]["rorg"] == int(RORG.VLD)
    assert discovery_info["eep_profile"]["rorg_func"] == 0x01
    assert discovery_info["eep_profile"]["rorg_type"] == 0x12
    assert discovery_info["eep_profile"]["manufacturer"] == 0x046

    # Clear dispatched list for next phase
    dispatched.clear()

    # Step 2: Simulate operational D2 packet (Query power command)
    d2_packet = DummyRadioPacket()
    d2_packet.rorg = RORG.VLD  # D2 operational packet
    d2_packet.sender = [0x05, 0x87, 0x5D, 0xB4]
    d2_packet.destination = [0xFF, 0xFF, 0xFF, 0xFF]  # Broadcast
    d2_packet.data = [0xD2, 0x04, 0x60, 0x80, 0x05, 0x87, 0x5D, 0xB4, 0x00]
    d2_packet.parsed = {
        "CMD": {"value": "Command ID 4", "raw_value": 4},
        "QU": {"value": "Query power", "raw_value": 1},
        "IO": {"value": "Output channel 0 (to load)", "raw_value": 0},
    }
    d2_packet.dBm = -86
    # These attributes don't exist on operational packets (only on UTE)
    d2_packet.rorg_of_eep = None
    d2_packet.rorg_manufacturer = None
    d2_packet.rorg_func = None
    d2_packet.rorg_type = None

    dongle.callback(d2_packet)
    await hass.async_block_till_done()

    # Verify operational packet was processed (dispatched to entities)
    receive_signals = [s for s, _ in dispatched if s == SIGNAL_RECEIVE_MESSAGE]
    assert len(receive_signals) >= 1, "Operational packet should be dispatched to entities"

    # Verify NO duplicate discovery was triggered
    discovery_signals = [s for s, _ in dispatched if s == SIGNAL_DISCOVER_DEVICE]
    assert len(discovery_signals) == 0, "Operational packet should not trigger discovery"

    # Verify profile is still correct (not overwritten with 0x00)
    assert device_key in dongle._device_profiles
    assert dongle._device_profiles[device_key]["rorg"] == int(RORG.VLD)
    assert dongle._device_profiles[device_key]["func"] == 0x01
    assert dongle._device_profiles[device_key]["type"] == 0x12

    # Cleanup
    dongle._communicator.stop()


@pytest.mark.parametrize("expected_lingering_tasks", [True])
@pytest.mark.timeout(5)
async def test_d2_operational_packet_ignored_without_ute_teachin(
    hass: HomeAssistant, mock_serial_communicator, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that D2 operational packets are ignored if no UTE teach-in occurred.
    
    This prevents spurious devices from operational packets during learning mode
    when the device profile hasn't been registered yet.
    """
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: "/dev/ttyUSB0", CONF_DEVICE_PROFILES: {}},
        unique_id="test_dongle",
    )
    config_entry.add_to_hass(hass)
    dongle = EnOceanDongle(hass, "/dev/ttyUSB0", config_entry)
    dongle._communicator.teach_in = True

    dispatched: list[tuple[str, dict]] = []

    def _fake_dispatcher_send(_hass, signal, payload):
        dispatched.append((signal, payload))

    monkeypatch.setattr(
        "custom_components.enocean.dongle.dispatcher_send", _fake_dispatcher_send
    )

    class DummyRadioPacket:
        """Simple stand-in for enocean RadioPacket."""

    monkeypatch.setattr("custom_components.enocean.dongle.RadioPacket", DummyRadioPacket)

    # Send operational D2 packet WITHOUT prior UTE teach-in
    d2_packet = DummyRadioPacket()
    d2_packet.rorg = RORG.VLD
    d2_packet.sender = [0x05, 0x87, 0x5D, 0xB4]
    d2_packet.destination = [0xFF, 0xFF, 0xFF, 0xFF]
    d2_packet.data = [0xD2, 0x04, 0x60, 0x80, 0x05, 0x87, 0x5D, 0xB4, 0x00]
    d2_packet.parsed = {"CMD": {"value": "Command ID 4"}}
    d2_packet.dBm = -86
    d2_packet.rorg_of_eep = None
    d2_packet.rorg_manufacturer = None
    d2_packet.rorg_func = None
    d2_packet.rorg_type = None

    dongle.callback(d2_packet)
    await hass.async_block_till_done()

    # Verify NO profile was registered
    device_key = tuple(d2_packet.sender)
    assert device_key not in dongle._device_profiles

    # Verify NO discovery was triggered
    discovery_signals = [s for s, _ in dispatched if s == SIGNAL_DISCOVER_DEVICE]
    assert len(discovery_signals) == 0

    # Verify NO receive signal was dispatched
    receive_signals = [s for s, _ in dispatched if s == SIGNAL_RECEIVE_MESSAGE]
    assert len(receive_signals) == 0

    # Cleanup
    dongle._communicator.stop()
