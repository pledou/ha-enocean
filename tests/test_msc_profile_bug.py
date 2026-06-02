"""Test for MSC device profile bug - invalid profiles should be rejected."""

from __future__ import annotations

from unittest.mock import Mock, patch, MagicMock

from enocean.protocol.constants import RORG, PACKET
from enocean.protocol.packet import RadioPacket, UTETeachInPacket
import pytest

from custom_components.enocean.const import CONF_DEVICE_PROFILES
from custom_components.enocean.dongle import EnOceanDongle
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry

DOMAIN = "enocean"


@pytest.fixture
def mock_serial_communicator():
    """Mock the SerialCommunicator."""
    with patch("custom_components.enocean.dongle.SerialCommunicator") as mock_comm:
        mock_instance = MagicMock()
        mock_instance.start = Mock()
        mock_instance.stop = Mock()
        mock_instance.base_id = [0xFF, 0x9C, 0x80, 0x80]
        mock_instance.teach_in = True  # Learning mode enabled by default
        mock_comm.return_value = mock_instance
        yield mock_instance


@pytest.mark.asyncio
async def test_reject_invalid_ute_profile_all_zeros(
    hass: HomeAssistant, mock_serial_communicator
) -> None:
    """Test that UTE teach-in with all-zero profile is rejected."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: "/dev/ttyUSB0", CONF_DEVICE_PROFILES: {}},
        unique_id="test_dongle",
    )
    config_entry.add_to_hass(hass)

    dongle = EnOceanDongle(hass, "/dev/ttyUSB0", config_entry)
    await dongle.async_setup(load_profiles=False)
    
    # Enable learning mode explicitly
    dongle._communicator.teach_in = True

    # Create a mock UTE teach-in packet with incomplete EEP info
    # This simulates a malformed or incomplete UTE packet
    mock_packet = Mock(spec=UTETeachInPacket)
    mock_packet.packet_type = PACKET.RADIO_ERP1
    mock_packet.rorg = RORG.UTE  # 0xD4
    mock_packet.sender = [0x04, 0x20, 0x58, 0xA5]
    mock_packet.destination = [0xFF, 0xFF, 0xFF, 0xFF]
    
    # Missing or invalid EEP info (this is what causes the bug)
    mock_packet.rorg_of_eep = None  # Should cause rorg_value = 0
    mock_packet.rorg_manufacturer = 0x079
    mock_packet.rorg_func = None  # Should cause func = 0
    mock_packet.rorg_type = None  # Should cause type = 0
    mock_packet.learn = True
    mock_packet.contains_eep = False

    # Simulate packet arrival during learning mode
    with patch.object(dongle, "_validate_and_track_packet", return_value=True):
        dongle.callback(mock_packet)

    # Verify that invalid profile was NOT registered
    device_key = (0x04, 0x20, 0x58, 0xA5)
    
    # The device should either:
    # 1. Not be in profiles at all (ideal), or
    # 2. Not have rorg=0 if it is registered
    if device_key in dongle._device_profiles:
        profile = dongle._device_profiles[device_key]
        assert profile["rorg"] != 0, "Invalid profile with rorg=0 was registered"
        assert profile["rorg"] != RORG.UNDEFINED, "UNDEFINED rorg was registered"


@pytest.mark.asyncio
async def test_reject_invalid_ute_profile_undefined_rorg(
    hass: HomeAssistant, mock_serial_communicator
) -> None:
    """Test that UTE teach-in with UNDEFINED rorg is rejected."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: "/dev/ttyUSB0", CONF_DEVICE_PROFILES: {}},
        unique_id="test_dongle",
    )
    config_entry.add_to_hass(hass)

    dongle = EnOceanDongle(hass, "/dev/ttyUSB0", config_entry)
    await dongle.async_setup(load_profiles=False)
    
    # Enable learning mode explicitly
    dongle._communicator.teach_in = True

    mock_packet = Mock(spec=UTETeachInPacket)
    mock_packet.packet_type = PACKET.RADIO_ERP1
    mock_packet.rorg = RORG.UTE
    mock_packet.sender = [0x04, 0x20, 0x74, 0xC9]
    mock_packet.destination = [0xFF, 0xFF, 0xFF, 0xFF]
    
    # Explicitly set UNDEFINED
    mock_packet.rorg_of_eep = RORG.UNDEFINED  # 0x00
    mock_packet.rorg_manufacturer = 0x079
    mock_packet.rorg_func = 0x00
    mock_packet.rorg_type = 0x00
    mock_packet.learn = True
    mock_packet.contains_eep = False

    with patch.object(dongle, "_validate_and_track_packet", return_value=True):
        dongle.callback(mock_packet)

    device_key = (0x04, 0x20, 0x74, 0xC9)
    
    if device_key in dongle._device_profiles:
        profile = dongle._device_profiles[device_key]
        assert profile["rorg"] != RORG.UNDEFINED, "UNDEFINED rorg was registered"


@pytest.mark.asyncio
async def test_msc_operational_packet_not_treated_as_ute(
    hass: HomeAssistant, mock_serial_communicator
) -> None:
    """Test that MSC operational packets are not treated as UTE teach-in."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: "/dev/ttyUSB0", CONF_DEVICE_PROFILES: {}},
        unique_id="test_dongle",
    )
    config_entry.add_to_hass(hass)

    dongle = EnOceanDongle(hass, "/dev/ttyUSB0", config_entry)
    await dongle.async_setup(load_profiles=False)
    
    # Enable learning mode explicitly
    dongle._communicator.teach_in = True

    # Create a mock MSC operational packet (not UTE)
    mock_packet = Mock(spec=RadioPacket)
    mock_packet.packet_type = PACKET.RADIO_ERP1
    mock_packet.rorg = RORG.MSC  # 0xD1 - This is an MSC packet, not UTE!
    mock_packet.sender = [0x04, 0x20, 0x58, 0xA5]
    mock_packet.destination = [0xFF, 0xFF, 0xFF, 0xFF]
    mock_packet.rorg_manufacturer = 0x079
    mock_packet.cmd = 0
    mock_packet.learn = False
    mock_packet.contains_eep = False
    mock_packet.parsed = {"CMD": {"value": "Command ID 0"}}

    # This should NOT trigger UTE discovery
    with patch.object(dongle, "_validate_and_track_packet", return_value=True):
        dongle.callback(mock_packet)

    # MSC packet should be ignored during learning mode (no profile registered)
    device_key = (0x04, 0x20, 0x58, 0xA5)
    assert device_key not in dongle._device_profiles, \
        "MSC operational packet incorrectly registered a profile during learning"


@pytest.mark.asyncio
async def test_valid_msc_ute_teach_in(
    hass: HomeAssistant, mock_serial_communicator
) -> None:
    """Test that valid MSC UTE teach-in with proper EEP info is accepted."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: "/dev/ttyUSB0", CONF_DEVICE_PROFILES: {}},
        unique_id="test_dongle",
    )
    config_entry.add_to_hass(hass)

    dongle = EnOceanDongle(hass, "/dev/ttyUSB0", config_entry)
    await dongle.async_setup(load_profiles=False)
    
    # Enable learning mode explicitly after setup
    dongle._communicator.teach_in = True

    # Create a proper UTE teach-in packet with valid MSC EEP info
    mock_packet = Mock(spec=UTETeachInPacket)
    mock_packet.packet_type = PACKET.RADIO_ERP1
    mock_packet.rorg = RORG.UTE  # 0xD4 - UTE teach-in
    mock_packet.sender = [0x04, 0x20, 0x58, 0xA5]
    mock_packet.destination = [0xFF, 0xFF, 0xFF, 0xFF]
    
    # Proper MSC EEP info
    mock_packet.rorg_of_eep = RORG.MSC  # 0xD1
    mock_packet.rorg_manufacturer = 0x079  # VentilAirSec
    mock_packet.rorg_func = 0x01
    mock_packet.rorg_type = 0x00
    mock_packet.learn = True
    mock_packet.contains_eep = True

    with patch.object(dongle, "_validate_and_track_packet", return_value=True):
        dongle.callback(mock_packet)

    # Valid MSC profile should be registered correctly
    device_key = (0x04, 0x20, 0x58, 0xA5)
    assert device_key in dongle._device_profiles
    
    profile = dongle._device_profiles[device_key]
    # Should be 0xD1079 (MSC with manufacturer)
    assert profile["rorg"] == 0xD1079
    assert profile["func"] == 0x01
    assert profile["type"] == 0x00


@pytest.mark.asyncio
async def test_load_invalid_persisted_profiles_are_rejected(
    hass: HomeAssistant, mock_serial_communicator
) -> None:
    """Test that invalid persisted profiles are rejected on load."""
    # Create config with invalid stored profiles
    invalid_profiles = {
        "4,32,88,165": {  # 04:20:58:a5
            "rorg": 0,  # Invalid!
            "func": 0,
            "type": 0,
        },
        "5,135,93,180": {  # Valid D2 device
            "rorg": 210,
            "func": 1,
            "type": 18,
        },
    }
    
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: "/dev/ttyUSB0", CONF_DEVICE_PROFILES: invalid_profiles},
        unique_id="test_dongle",
    )
    config_entry.add_to_hass(hass)

    dongle = EnOceanDongle(hass, "/dev/ttyUSB0", config_entry)
    await dongle.async_setup(load_profiles=True)

    # Only valid profile should be loaded
    assert (5, 135, 93, 180) in dongle._device_profiles
    
    # Invalid profile should NOT be loaded
    assert (4, 32, 88, 165) not in dongle._device_profiles, \
        "Invalid persisted profile with rorg=0 was loaded"


@pytest.mark.asyncio
async def test_ute_teachin_skipped_for_already_paired_device(
    hass: HomeAssistant, mock_serial_communicator
) -> None:
    """Test that UTE teach-in is skipped if device already has entities (already paired).
    
    This prevents accidental re-pairing and profile corruption when learning mode
    is enabled with already-paired devices.
    """
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE: "/dev/ttyUSB0", CONF_DEVICE_PROFILES: {}},
        unique_id="test_dongle",
    )
    config_entry.add_to_hass(hass)

    dongle = EnOceanDongle(hass, "/dev/ttyUSB0", config_entry)
    await dongle.async_setup(load_profiles=False)

    # Enable learning mode
    dongle._communicator.teach_in = True

    # Pre-register device as having entities (simulating already paired device)
    device_id = [0x04, 0x20, 0x58, 0xA5]
    dongle.mark_device_has_entities(device_id)

    # Store the current profile count
    initial_profile_count = len(dongle._device_profiles)

    # Create a UTE teach-in packet from already-paired device
    mock_packet = Mock(spec=UTETeachInPacket)
    mock_packet.packet_type = PACKET.RADIO_ERP1
    mock_packet.rorg = RORG.UTE
    mock_packet.sender = device_id
    mock_packet.destination = [0xFF, 0xFF, 0xFF, 0xFF]
    mock_packet.rorg_of_eep = RORG.MSC
    mock_packet.rorg_manufacturer = 0x079
    mock_packet.rorg_func = 0x01
    mock_packet.rorg_type = 0x00
    mock_packet.learn = True
    mock_packet.contains_eep = True

    with patch.object(dongle, "_validate_and_track_packet", return_value=True):
        dongle.callback(mock_packet)

    # Verify that NO new profile was registered (device was already paired)
    assert len(dongle._device_profiles) == initial_profile_count
    device_key = tuple(device_id)
    assert device_key not in dongle._device_profiles, \
        "UTE teach-in from already-paired device incorrectly registered a profile"
