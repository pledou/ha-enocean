"""Representation of an EnOcean dongle."""

import asyncio
from collections.abc import Callable
import contextlib
import glob
import logging
from os.path import basename, normpath

from enocean.communicators import SerialCommunicator
from enocean.protocol.constants import PACKET, RORG
from enocean.protocol.packet import Packet, RadioPacket
import serial

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
    dispatcher_send,
)

from .const import CONF_DEVICE_PROFILES, SIGNAL_RECEIVE_MESSAGE, SIGNAL_SEND_MESSAGE
from .entity import format_device_id_hex
from .types import DiscoveryInfo

_LOGGER = logging.getLogger(__name__)

SIGNAL_DISCOVER_DEVICE = "enocean.discover_device"
SIGNAL_LEARNING_MODE_CHANGED = "enocean.learning_mode_changed"


class EnOceanDongle:
    """Representation of an EnOcean dongle.

    The dongle is responsible for receiving the EnOcean frames,
    creating devices if needed, and dispatching messages to platforms.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        serial_path: str,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the EnOcean dongle."""

        self._communicator = SerialCommunicator(
            port=serial_path, callback=self.callback
        )
        self._communicator.teach_in = False  # Start with learning mode disabled
        self.serial_path = serial_path
        self.identifier = basename(normpath(serial_path))
        self.hass = hass
        self.config_entry = config_entry
        self.dispatcher_disconnect_handle: Callable[[], None] | None = None
        self._discovered_sensors: dict = {}  # Track discovered sensors by (parent_id, sensor_id)
        self._device_profiles: dict[
            tuple[int, ...], dict
        ] = {}  # Track EEP profiles by device_id
        self._devices_with_entities: set[tuple[int, ...]] = (
            set()
        )  # Track devices that have entities
        self._learning_task: asyncio.Task[None] | None = None
        self._learning_duration = 10  # Default 10 minutes
        self.base_id: list[int] | None = None
        # Track consecutive invalid packets per device for dongle reset logic
        self._device_invalid_packet_count: dict[str, int] = {}
        self._dongle_reset_threshold = 5  # Reset after 10 consecutive invalid packets
        # Track device-level warning flags to avoid duplicate warnings
        self._device_warnings: dict[str, dict[str, bool]] = {}

    def has_entity_for_device(self, device_id) -> bool:
        """Check if the given device_id already has entities created."""
        device_key = tuple(device_id) if isinstance(device_id, list) else (device_id,)
        return device_key in self._devices_with_entities

    def remove_entity_for_device(self, device_id) -> bool:
        """Remove the given device_id from tracking of devices with entities.

        This allows the device to be rediscovered and have new entities created
        if it sends packets again. Also removes the persisted device profile.
        Returns True if the device was previously marked as having entities, 
        False otherwise.
        """
        device_key = tuple(device_id) if isinstance(device_id, list) else (device_id,)
        removed = False
        
        if device_key in self._devices_with_entities:
            self._devices_with_entities.remove(device_key)
            removed = True
            _LOGGER.debug(
                "Removed device %s from tracking of devices with entities",
                format_device_id_hex(list(device_key)),
            )
        
        # Also remove persisted profile so device can be re-learned
        if device_key in self._device_profiles:
            del self._device_profiles[device_key]
            removed = True
            _LOGGER.info(
                "Removed persisted EEP profile for device %s (can now be re-learned)",
                format_device_id_hex(list(device_key)),
            )
            # Save updated profiles to config entry
            if self.hass:
                self.hass.loop.call_soon_threadsafe(self._async_save_device_profiles)
        
        return removed

    def has_device_entities(self, device_id) -> bool:
        """Compatibility wrapper: return True if device already has entities.

        Some callers expect the method name `has_device_entities`; keep a
        thin wrapper to preserve the external API.
        """
        return self.has_entity_for_device(device_id)

    async def async_setup(self, load_profiles: bool = True):
        """Finish the setup of the bridge and supported platforms.

        Args:
            load_profiles: If True, load persisted device profiles immediately.
                          If False, caller must call async_load_device_profiles() later.
        """
        self._communicator.start()

        # Pre-fetch base ID to avoid deadlock when UTE teach-in arrives
        # This must be done after start() so the communicator thread is running
        await self.hass.async_add_executor_job(self._fetch_base_id)

        # Load previously learned device profiles from config entry storage
        # unless the caller wants to defer this until after platforms are set up
        if load_profiles:
            await self.async_load_device_profiles()

        self.dispatcher_disconnect_handle = async_dispatcher_connect(
            self.hass, SIGNAL_SEND_MESSAGE, self._send_message_callback
        )

    def _fetch_base_id(self):
        """Fetch base ID from the dongle (runs in executor)."""
        self.base_id = self._communicator.base_id
        if self.base_id:
            _LOGGER.debug(
                "EnOcean Base ID: %s",
                self.base_id.hex() if isinstance(self.base_id, bytes) else self.base_id,
            )
        else:
            _LOGGER.warning("Could not retrieve EnOcean Base ID from dongle")

    def unload(self):
        """Disconnect callbacks established at init time."""
        if self.dispatcher_disconnect_handle:
            self.dispatcher_disconnect_handle()
            self.dispatcher_disconnect_handle = None

        # Cancel any pending learning mode timeout
        if self._learning_task:
            self._learning_task.cancel()

    @property
    def learning_duration(self) -> int:
        """Return the current learning duration in minutes."""
        return self._learning_duration

    @learning_duration.setter
    def learning_duration(self, value: int) -> None:
        """Set the learning duration in minutes."""
        self._learning_duration = value

    async def async_start_learning(self, duration: int | None = None) -> None:
        """Enable learning mode for the specified duration.

        Args:
            duration: Time in minutes to keep learning mode enabled. If None, uses the configured duration.
        """
        if duration is None:
            duration = self._learning_duration
        self._communicator.teach_in = True
        _LOGGER.info("EnOcean learning mode enabled for %d minutes", duration)

        # Enable automatic UTE teach-in responses during learning mode
        # The dongle will properly respond with 0x91 (EEP Teach-In Response) to UTE queries (0xA0)
        # This signals to the device that the dongle has learned its EEP profile
        self._communicator.automatic_answer = True

        # Send dispatcher signal that learning mode started
        dispatcher_send(
            self.hass,
            SIGNAL_LEARNING_MODE_CHANGED,
            {"enabled": True, "duration": duration * 60},  # duration in seconds
        )

        # Cancel previous learning task if it exists
        if self._learning_task:
            self._learning_task.cancel()

        # Schedule learning mode to be disabled after the specified duration
        self._learning_task = asyncio.create_task(
            self._disable_learning_after_timeout(duration * 60)  # duration in seconds
        )

    async def async_stop_learning(self) -> None:
        """Stop learning mode immediately."""
        if self._learning_task:
            self._learning_task.cancel()
        self._communicator.teach_in = False

        _LOGGER.info("EnOcean learning mode disabled (user stop)")

        # Send dispatcher signal that learning mode stopped
        dispatcher_send(
            self.hass,
            SIGNAL_LEARNING_MODE_CHANGED,
            {"enabled": False},
        )

    async def _disable_learning_after_timeout(self, duration: int) -> None:
        """Disable learning mode after timeout."""
        try:
            await asyncio.sleep(duration)
        except asyncio.CancelledError:
            return
        self._communicator.teach_in = False
        _LOGGER.info("EnOcean learning mode disabled (timeout)")

        # Send dispatcher signal that learning mode stopped
        dispatcher_send(
            self.hass,
            SIGNAL_LEARNING_MODE_CHANGED,
            {"enabled": False},
        )

    def _get_device_warnings(self, device_id: list[int]) -> dict[str, bool]:
        """Get device-level warning flags.

        Returns dict with keys: out_of_range_logged, invalid_enum_logged
        This ensures warnings are logged once per device, not once per entity.

        Args:
            device_id: Device ID as list of integers

        Returns:
            Dictionary with warning flag states
        """
        device_id_str = format_device_id_hex(device_id)

        if device_id_str not in self._device_warnings:
            self._device_warnings[device_id_str] = {
                "out_of_range_logged": False,
                "invalid_enum_logged": False,
            }

        return self._device_warnings[device_id_str]

    def _has_out_of_range_fields(self, packet) -> bool:
        """Check if packet has any fields with out-of-range values.

        Args:
            packet: EnOcean packet with parsed data

        Returns:
            True if any field is out of range, False otherwise
        """
        if not hasattr(packet, "parsed") or not packet.parsed:
            return False

        for field_data in packet.parsed.values():
            if isinstance(field_data, dict) and field_data.get("out_of_range", False):
                return True
        return False

    def _has_invalid_enum_fields(self, packet) -> bool:
        """Check if packet has any enum fields with invalid values.

        Args:
            packet: EnOcean packet with parsed data

        Returns:
            True if any enum field has invalid value, False otherwise
        """
        if not hasattr(packet, "parsed") or not packet.parsed:
            return False

        for field_data in packet.parsed.values():
            if isinstance(field_data, dict) and field_data.get("invalid_enum", False):
                return True
        return False

    def _log_invalid_packet_warning(self, packet):
        """Log warning for packets with out-of-range values.

        Args:
            packet: EnOcean packet with invalid data
        """
        try:
            sender = format_device_id_hex(
                packet.sender if hasattr(packet, "sender") else [0, 0, 0, 0]
            )
            dbm = packet.dBm if hasattr(packet, "dBm") else 0

            # Collect out-of-range fields info
            out_of_range_fields = []
            all_parsed_values = {}
            if packet.parsed:
                for field_name, field_data in packet.parsed.items():
                    if isinstance(field_data, dict):
                        value = field_data.get("value", field_data.get("raw_value"))
                        all_parsed_values[field_name] = value
                        if field_data.get("out_of_range", False):
                            raw_value = field_data.get("raw_value")
                            unit = field_data.get("unit", "")
                            out_of_range_fields.append(
                                f"{field_name}={value} (raw={raw_value}){f' {unit}' if unit else ''}"
                            )
                    else:
                        all_parsed_values[field_name] = field_data

            _LOGGER.warning(
                "Ignoring packet from %s with out-of-range fields: [%s]. All values: %s (Signal: %d dBm)",
                sender,
                ", ".join(out_of_range_fields),
                all_parsed_values,
                dbm,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Error formatting invalid packet warning: %s", err)

    def _log_invalid_enum_warning(self, packet):
        """Log warning for packets with invalid enum values.

        Args:
            packet: EnOcean packet with invalid enum data
        """
        try:
            sender = format_device_id_hex(
                packet.sender if hasattr(packet, "sender") else [0, 0, 0, 0]
            )
            dbm = packet.dBm if hasattr(packet, "dBm") else 0

            # Collect invalid enum fields info
            invalid_enum_fields = []
            all_parsed_values = {}
            if packet.parsed:
                for field_name, field_data in packet.parsed.items():
                    if isinstance(field_data, dict):
                        value = field_data.get("value", field_data.get("raw_value"))
                        all_parsed_values[field_name] = value
                        if field_data.get("invalid_enum", False):
                            raw_value = field_data.get("raw_value")
                            unit = field_data.get("unit", "")
                            invalid_enum_fields.append(
                                f"{field_name}={value} (raw={raw_value}){f' {unit}' if unit else ''}"
                            )
                    else:
                        all_parsed_values[field_name] = field_data

            _LOGGER.warning(
                "Ignoring packet from %s with invalid enum values: [%s]. All values: %s (Signal: %d dBm)",
                sender,
                ", ".join(invalid_enum_fields),
                all_parsed_values,
                dbm,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Error formatting invalid enum warning: %s", err)

    def _validate_and_track_packet(self, packet) -> bool:
        """Validate packet and track invalid counts.

        Args:
            packet: EnOcean packet to validate

        Returns:
            True if packet is valid and should be dispatched, False otherwise
        """
        if not hasattr(packet, "sender"):
            return True  # Can't validate without sender ID

        device_id = packet.sender
        device_id_str = format_device_id_hex(device_id)
        device_warnings = self._get_device_warnings(device_id)

        # Check if any parsed fields are out of range
        if self._has_out_of_range_fields(packet):
            # Only log detailed warning once per device to avoid spam
            if not device_warnings["out_of_range_logged"]:
                self._log_invalid_packet_warning(packet)
                device_warnings["out_of_range_logged"] = True

            # Increment invalid packet count
            self._device_invalid_packet_count[device_id_str] = (
                self._device_invalid_packet_count.get(device_id_str, 0) + 1
            )

            # Check if threshold exceeded and trigger reset
            if (
                self._device_invalid_packet_count[device_id_str]
                >= self._dongle_reset_threshold
            ):
                self._trigger_dongle_reset(device_id_str)

            return False

        # Check if any enum fields have invalid values
        if self._has_invalid_enum_fields(packet):
            # Only log detailed warning once per device to avoid spam
            if not device_warnings["invalid_enum_logged"]:
                self._log_invalid_enum_warning(packet)
                device_warnings["invalid_enum_logged"] = True

            # Increment invalid packet count
            self._device_invalid_packet_count[device_id_str] = (
                self._device_invalid_packet_count.get(device_id_str, 0) + 1
            )

            # Check if threshold exceeded and trigger reset
            if (
                self._device_invalid_packet_count[device_id_str]
                >= self._dongle_reset_threshold
            ):
                self._trigger_dongle_reset(device_id_str)

            return False

        # Reset warning flags and counter if we receive valid data
        # device_warnings["out_of_range_logged"] = False
        # device_warnings["invalid_enum_logged"] = False
        # self._device_invalid_packet_count.pop(device_id_str, None)

        return True

    def reset_invalid_packet_count(self, device_id: list[int]) -> None:
        """Reset consecutive invalid packet count for a device when valid data received.

        Args:
            device_id: Device ID as list of integers
        """
        device_id_str = format_device_id_hex(device_id)
        self._device_invalid_packet_count.pop(device_id_str, None)

    def _trigger_dongle_reset(self, device_id_str: str) -> None:
        """Trigger dongle reset via CO_WR_RESET command.

        Args:
            device_id_str: Device ID as hex string for logging
        """
        count = self._device_invalid_packet_count.get(device_id_str, 0)
        _LOGGER.warning(
            "Device %s has received %d consecutive invalid packets. Attempting dongle reset (CO_WR_RESET)",
            device_id_str,
            count,
        )

        try:
            # Create CO_WR_RESET common command packet
            # CO_WR_RESET = 0x02 (Common Command 2)
            reset_packet = Packet(PACKET.COMMON_COMMAND, data=[0x02])

            _LOGGER.info("Sending CO_WR_RESET command to EnOcean dongle")

            # Use call_soon_threadsafe since we're in the communicator thread
            self.hass.loop.call_soon_threadsafe(
                dispatcher_send, self.hass, SIGNAL_SEND_MESSAGE, reset_packet
            )

            # Clear all device counters and warnings after reset
            self._device_invalid_packet_count.clear()
            self._device_warnings.clear()

            _LOGGER.info("Dongle reset command sent successfully")
        except Exception:
            _LOGGER.exception(
                "Failed to send dongle reset command",
            )
            self._communicator.teach_in = False
            _LOGGER.info("EnOcean learning mode disabled (timeout)")

            # Send dispatcher signal that learning mode ended
            dispatcher_send(
                self.hass,
                SIGNAL_LEARNING_MODE_CHANGED,
                {"enabled": False, "reason": "timeout"},
            )
        except asyncio.CancelledError:
            _LOGGER.debug("Learning mode timeout cancelled")
        finally:
            self._learning_task = None

    @property
    def learning_mode(self) -> bool:
        """Return whether learning mode is currently enabled."""
        return self._communicator.teach_in

    def _send_message_callback(self, command):
        """Send a command through the EnOcean dongle."""
        self._communicator.send(command)

    def _infer_rps_teach_in_profile(self, packet) -> tuple[int, int]:
        """Infer a likely FUNC/TYPE pair for RPS teach-in without UTE.

        Stateless buttons may not emit UTE telegrams. During learning mode,
        infer a practical profile from the action byte so pairing can proceed.
        """
        action = 0
        if hasattr(packet, "data") and len(packet.data) > 1:
            with contextlib.suppress(TypeError, ValueError):
                action = int(packet.data[1])

        # Common rocker button actions (F6-02-01 / style 2)
        if action in {0x70, 0x50, 0x30, 0x10, 0x37, 0x15}:
            return (0x02, 0x01)

        # Window handle action families use nibble values 4/5/6/7
        action_nibble = (action & 0x70) >> 4
        if action_nibble in {0x04, 0x05, 0x06, 0x07}:
            return (0x10, 0x00)

        # Unknown RPS subtype: keep generic FUNC/TYPE to allow fallback mapping
        return (0x00, 0x00)

    def callback(self, packet):
        """Handle EnOcean device's callback.

        This is the callback function called by python-enocean whenever there
        is an incoming packet. This runs in a background thread, so we need to
        schedule all dispatcher sends through the event loop to avoid task context issues.
        """

        if isinstance(packet, RadioPacket):
            # Safely obtain optional attributes from packet to avoid AttributeError
            rorg_of_eep_val = getattr(packet, "rorg_of_eep", None)
            rorg_manuf_val = getattr(packet, "rorg_manufacturer", None)
            rorg_func = getattr(packet, "rorg_func", None)
            rorg_type = getattr(packet, "rorg_type", None)

            # Systematically parse packets based on known EEP profiles
            # This ensures packet.parsed is populated before dispatching to entities
            # Skip UTE teach-in packets (0xD4) - they don't have data profiles
            if packet.rorg != RORG.UTE:
                self._parse_packet_by_profile(packet)

            # Trigger discovery for new devices
            device_id = packet.sender

            # For learning mode we only accept UTE teach-in packets (0xD4)
            # Handle teach-in before dispatching general receive signals so
            # the registered profile is available to listeners.
            if self._communicator.teach_in:
                if packet.rorg != RORG.UTE:
                    if packet.rorg == RORG.RPS:
                        # If the device is already paired with entities, skip teach-in
                        # but still process the packet for normal entity updates below
                        if self.has_device_entities(device_id):
                            _LOGGER.debug(
                                "Learning mode: RPS device %s already known, processing packet normally",
                                format_device_id_hex(device_id),
                            )
                            # Don't return - let packet continue to normal processing
                        else:
                            # New device - perform RPS teach-in
                            inferred_func, inferred_type = self._infer_rps_teach_in_profile(
                                packet
                            )
                            _LOGGER.info(
                                "Learning mode: inferred stateless RPS profile for %s as rorg=0x%02x func=0x%02x type=0x%02x",
                                format_device_id_hex(device_id),
                                int(RORG.RPS),
                                inferred_func,
                                inferred_type,
                            )

                            discovery_info: DiscoveryInfo = {
                                "device_id": device_id,
                                "eep_profile": {
                                    "rorg": int(RORG.RPS),
                                    "rorg_func": inferred_func,
                                    "rorg_type": inferred_type,
                                    "manufacturer": None,
                                },
                            }

                            self.register_device_profile(
                                device_id, int(RORG.RPS), inferred_func, inferred_type
                            )

                            self.hass.loop.call_soon_threadsafe(
                                lambda: dispatcher_send(
                                    self.hass, SIGNAL_DISCOVER_DEVICE, discovery_info
                                )
                            )
                        # For non-UTE learning packets (RPS), continue to normal processing
                        # instead of returning - this allows entities to receive updates
                    else:
                        # Non-RPS, non-UTE packet during learning mode - ignore
                        return

                if (rorg_of_eep_val == RORG.MSC) and (rorg_manuf_val is not None):
                    rorg_value = int(f"{rorg_of_eep_val:02x}{rorg_manuf_val:03x}", 16)
                else:
                    rorg_value = int(
                        rorg_of_eep_val if rorg_of_eep_val is not None else 0
                    )

                discovery_info: DiscoveryInfo = {
                    "device_id": device_id,
                    "eep_profile": {
                        "rorg": rorg_value,
                        "rorg_func": rorg_func if rorg_func is not None else 0,
                        "rorg_type": rorg_type if rorg_type is not None else 0,
                        "manufacturer": rorg_manuf_val,
                    },
                }

                # Register device profile for future packet parsing
                self.register_device_profile(
                    device_id, rorg_value, rorg_func or 0, rorg_type or 0
                )

                # Schedule discovery signal in event loop thread-safely
                self.hass.loop.call_soon_threadsafe(
                    lambda: dispatcher_send(
                        self.hass, SIGNAL_DISCOVER_DEVICE, discovery_info
                    )
                )

                # Do not dispatch the generic receive signal for teach-in packets
                return

            # Validate packet before dispatching
            if not self._validate_and_track_packet(packet):
                # Packet is invalid, don't dispatch to entities
                return

            # Non-teach-in: Dispatch RSSI update if present
            if hasattr(packet, "dBm") and packet.dBm is not None:
                device_id = packet.sender
                # Dispatch RSSI update to registered RSSI sensor entities
                self.hass.loop.call_soon_threadsafe(
                    lambda: dispatcher_send(
                        self.hass,
                        f"{SIGNAL_RECEIVE_MESSAGE}_rssi_{format_device_id_hex(device_id)}",
                        packet.dBm,
                    )
                )

            # Schedule message dispatch in event loop thread-safely
            # Only valid packets reach this point
            self.hass.loop.call_soon_threadsafe(
                lambda: dispatcher_send(self.hass, SIGNAL_RECEIVE_MESSAGE, packet)
            )

            # Process sensors from Ventilairsec MSC packets (Command 8)
            # This extracts sensor information and creates child devices/entities
            self._process_ventilairsec_sensors(packet)

    def register_device_profile(self, device_id, rorg: int, func: int, type_: int):
        """Register EEP profile for a device to enable systematic parsing.

        Args:
            device_id: Device ID (list of ints or bytes)
            rorg: RORG value
            func: FUNC value
            type_: TYPE value
        """
        device_key = tuple(device_id) if isinstance(device_id, list) else (device_id,)
        self._device_profiles[device_key] = {
            "rorg": rorg,
            "func": func,
            "type": type_,
        }
        # Persist the profile to config entry storage (thread-safe)
        if self.config_entry:
            self.hass.loop.call_soon_threadsafe(self._async_save_device_profiles)

    def mark_device_has_entities(self, device_id):
        """Mark that a device has entities created.

        This prevents repeated rediscovery attempts for devices that
        already have entities in Home Assistant.

        Args:
            device_id: Device ID (list of ints or tuple)
        """
        device_key = tuple(device_id) if isinstance(device_id, list) else (device_id,)
        self._devices_with_entities.add(device_key)

    async def async_load_device_profiles(self) -> None:
        """Load device profiles from config entry storage.

        Device profiles are stored in config_entry.data to survive
        Home Assistant restarts, ensuring that previously learned MSC devices
        can be parsed correctly when the integration is reloaded.

        This method can be called after platform setup to ensure platform
        callbacks are registered before discovery signals are dispatched.
        """
        if not self.config_entry:
            return

        stored_profiles = self.config_entry.data.get(CONF_DEVICE_PROFILES, {})

        # Convert string keys back to tuples of ints
        for device_key_str, profile in stored_profiles.items():
            try:
                # Parse the string representation of device key tuple back to tuple of ints
                device_key = tuple(int(x) for x in device_key_str.split(","))

                # Validate profile values are integers
                rorg = int(profile.get("rorg", 0))
                func = int(profile.get("func", 0))
                type_ = int(profile.get("type", 0))

                # Store validated profile with integer values
                self._device_profiles[device_key] = {
                    "rorg": rorg,
                    "func": func,
                    "type": type_,
                }
                _LOGGER.debug(
                    "Loaded persisted EEP profile for device %s: rorg=0x%02X func=0x%02X type=0x%02X",
                    format_device_id_hex(list(device_key)),
                    rorg,
                    func,
                    type_,
                )
                # Emit discovery for persisted profiles so platforms can
                # recreate entities after Home Assistant restart. This uses
                # the same dispatcher signal as live discovery to keep the
                # discovery flow centralized in async_setup_entry.
                try:
                    discovery_info = {
                        "device_id": list(device_key),
                        "eep_profile": {
                            "rorg": rorg,
                            "rorg_func": func,
                            "rorg_type": type_,
                            "manufacturer": profile.get("manufacturer"),
                        },
                    }
                    _LOGGER.info(
                        "Dispatching discovery for persisted device %s (rorg=0x%02X, func=0x%02X, type=0x%02X)",
                        format_device_id_hex(list(device_key)),
                        rorg,
                        func,
                        type_,
                    )
                    # Use async_dispatcher_send since we're in an async method
                    # and the handler is async
                    async_dispatcher_send(
                        self.hass, SIGNAL_DISCOVER_DEVICE, discovery_info
                    )
                except Exception:
                    _LOGGER.exception(
                        "Failed to dispatch discovery for persisted EnOcean device %s",
                        format_device_id_hex(list(device_key)),
                    )
            except (ValueError, KeyError, TypeError) as err:
                _LOGGER.warning(
                    "Failed to load device profile %s: %s", device_key_str, err
                )

    def _async_save_device_profiles(self) -> None:
        """Save device profiles to config entry storage.

        Persists all known device profiles to config_entry.data
        so they survive Home Assistant restarts.
        """
        if not self.config_entry:
            return

        # Convert device keys (tuples) to string representation for JSON storage
        profiles_to_save = {}
        for device_key, profile in self._device_profiles.items():
            key_str = ",".join(str(x) for x in device_key)
            profiles_to_save[key_str] = profile

        # Update config entry data with device profiles
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_DEVICE_PROFILES: profiles_to_save},
        )

    def _parse_packet_by_profile(self, packet):
        """Parse packet systematically based on known device EEP profile.

        This ensures packet.parsed is populated before dispatching to entities.
        If the device profile is known, creates a parser and parses the packet data.
        For reconstructed packets, ensures EEP data is parsed when profile is available.

        Args:
            packet: EnOcean RadioPacket to parse
        """
        # Skip command packets sent FROM controller (d1079-01-00) TO other devices
        # This avoids parsing controller→sensor commands that cause validation errors
        if hasattr(packet, "destination") and packet.destination:
            dest = (
                packet.destination
                if isinstance(packet.destination, list)
                else [packet.destination]
            )

            # Check if destination is broadcast
            is_broadcast = all(b == 0xFF for b in dest) or (
                len(dest) == 4 and dest[0] == 0xFF
            )

            # Check if destination is this dongle
            is_to_dongle = (
                dest == list(self.base_id) 
                if self.base_id and len(dest) == len(self.base_id) 
                else False
            )

            # Check if sender is controller (d1079-01-00 profile)
            sender_key = (
                tuple(packet.sender)
                if isinstance(packet.sender, list)
                else (packet.sender,)
            )
            sender_profile = self._device_profiles.get(sender_key)
            is_from_controller = (
                sender_profile
                and sender_profile.get("rorg") == 0xD1079
                and sender_profile.get("func") == 0x01
            )

            # Only skip if it's FROM controller AND directed to another device
            if is_from_controller and not is_broadcast and not is_to_dongle:
                # This is a command packet from controller to sensor - skip parsing
                _LOGGER.debug(
                    "Skipping parse of controller command from %s to %s (not to dongle %s)",
                    format_device_id_hex(packet.sender)
                    if hasattr(packet, "sender")
                    else "unknown",
                    format_device_id_hex(dest),
                    format_device_id_hex(list(self.base_id)),
                )
                return

        # Get device key
        device_key = (
            tuple(packet.sender)
            if isinstance(packet.sender, list)
            else (packet.sender,)
        )

        # Look up profile for this device
        profile = self._device_profiles.get(device_key)

        if not profile:
            # No known profile for this device yet
            return

        # Extract command if present (for MSC and VLD packets)
        # For MSC packets, packet.cmd is already set by the enocean library
        # during packet.parse() - it extracts CMD from bits 12-15 for Ventilairsec
        command = getattr(packet, "cmd", None)

        try:
            # Use the packet's own parse_eep method which populates packet.parsed
            # with the full nested structure expected by entities
            packet.parse_eep(
                rorg_func=profile["func"],
                rorg_type=profile["type"],
                direction=None,
                command=command,
            )

            if packet.parsed:
                # If device has a profile but no entities yet, trigger rediscovery
                # This happens after restart when persisted profiles are loaded
                if device_key not in self._devices_with_entities:
                    _LOGGER.info(
                        "Device %s has no entities yet, triggering rediscovery",
                        format_device_id_hex(list(device_key)),
                    )
                    discovery_info = {
                        "device_id": list(device_key),
                        "eep_profile": {
                            "rorg": profile["rorg"],
                            "rorg_func": profile["func"],
                            "rorg_type": profile["type"],
                            "manufacturer": profile.get("manufacturer"),
                        },
                    }
                    # Schedule discovery signal in event loop thread-safely
                    self.hass.loop.call_soon_threadsafe(
                        lambda: async_dispatcher_send(
                            self.hass, SIGNAL_DISCOVER_DEVICE, discovery_info
                        )
                    )
        except (ValueError, TypeError, OSError) as err:
            _LOGGER.debug(
                "Failed to parse packet from %s: %s",
                format_device_id_hex(packet.sender)
                if isinstance(packet.sender, list)
                else packet.sender,
                err,
            )

    def _process_ventilairsec_sensors(self, packet):
        """Process sensors from Ventilairsec MSC Command 8 (Capteurs détectés).

        This method extracts sensor information from already-parsed MSC packets
        with command 8 and creates discovery signals for each detected sensor.

        Args:
            packet: EnOcean RadioPacket with parsed data (already parsed by _parse_packet_by_profile)
        """
        # Only process Ventilairsec MSC command 8 packets (sensor discovery)
        rorg_manuf_val = getattr(packet, "rorg_manufacturer", None)
        if (
            packet.rorg != RORG.MSC
            or rorg_manuf_val != 0x079
            or packet.cmd != 8
            or not packet.parsed
        ):
            return

        parent_device_id = packet.sender

        # Extract sensor information from parsed packet
        try:
            # IDAPP: Unique sensor ID (32-bit)
            sensor_id_raw = packet.parsed.get("IDAPP", {})
            if not sensor_id_raw:
                return

            # Extract value from dict structure if present
            if isinstance(sensor_id_raw, dict):
                sensor_id = sensor_id_raw.get("value", sensor_id_raw.get("raw_value"))
            else:
                sensor_id = sensor_id_raw

            # Convert to int if it's a float (parser may return float)
            if isinstance(sensor_id, (float, int)):
                sensor_id = int(sensor_id)
            else:
                _LOGGER.warning("Invalid sensor_id type: %s", type(sensor_id))
                return

            # PROFAPP: Sensor profile (enum: 1=MSC, 2=A5_04_01, 3=A5_09_04, 4=D2_04_08)
            prof_app_raw = packet.parsed.get("PROFAPP", {})
            prof_app_value = (
                prof_app_raw.get("raw_value", prof_app_raw.get("value"))
                if isinstance(prof_app_raw, dict)
                else prof_app_raw
            )

            # CAPTINDEX: Sensor index (for multiple sensors)
            capt_index_raw = packet.parsed.get("CAPTINDEX", {})
            capt_index = (
                capt_index_raw.get("value", capt_index_raw.get("raw_value"))
                if isinstance(capt_index_raw, dict)
                else capt_index_raw
            )

            _LOGGER.debug(
                "MSC CMD=8 extracted - sensor_id=%s, prof_app_value=%s, capt_index=%s",
                sensor_id,
                prof_app_value,
                capt_index,
            )

            # Create unique sensor key based on parent device + sensor ID
            # Convert parent_device_id to tuple of ints for hashable key
            parent_key = (
                tuple(parent_device_id)
                if isinstance(parent_device_id, list)
                else (parent_device_id,)
            )
            sensor_key = (parent_key, sensor_id)

            # Skip if already discovered
            if sensor_key in self._discovered_sensors:
                return

            # Mark as discovered
            self._discovered_sensors[sensor_key] = True

            # Map PROFAPP values to device type strings
            prof_map = {
                1: "d1079-00-00",  # MSC Assistant Ventilairsec
                2: "a5-04-01",  # Temperature + Humidity Pilot
                3: "a5-09-04",  # CO2 + Temperature + Humidity
                4: "d2-04-08",  # Nanosense E4000
            }

            device_type = prof_map.get(prof_app_value, f"unknown-{prof_app_value}")

            _LOGGER.info(
                "Sensor detected from Ventilairsec %s: ID=%s, Profile=%s, Index=%d",
                format_device_id_hex(parent_device_id)
                if isinstance(parent_device_id, list)
                else parent_device_id,
                f"{sensor_id:08X}",
                device_type,
                capt_index,
            )

            # Build string-based EEP profile parts (e.g. 'd1079','00','00')
            eep_parts = device_type.split("-")
            eep_rorg_str = eep_parts[0]
            eep_func_str = eep_parts[1] if len(eep_parts) > 1 else "00"
            eep_type_str = eep_parts[2] if len(eep_parts) > 2 else "00"

            # Convert sensor_id integer to list of 4 bytes in big-endian order to match packet.sender format
            # packet.sender is [MSB, ..., LSB], so extract bytes from most to least significant
            sensor_id_bytes = [
                (sensor_id >> 24) & 0xFF,  # Most significant byte
                (sensor_id >> 16) & 0xFF,
                (sensor_id >> 8) & 0xFF,
                sensor_id & 0xFF,  # Least significant byte
            ]

            # Parse EEP profile strings to integers and register the profile
            try:
                rorg_int = (
                    int(eep_rorg_str, 16)
                    if isinstance(eep_rorg_str, str)
                    else int(eep_rorg_str)
                )
                func_int = (
                    int(eep_func_str, 16)
                    if isinstance(eep_func_str, str)
                    else int(eep_func_str)
                )
                type_int = (
                    int(eep_type_str, 16)
                    if isinstance(eep_type_str, str)
                    else int(eep_type_str)
                )
            except (ValueError, KeyError, TypeError):
                _LOGGER.warning(
                    "Invalid EEP profile strings for sensor ID %s: rorg=%s, func=%s, type=%s",
                    f"{sensor_id:08X}",
                    eep_rorg_str,
                    eep_func_str,
                    eep_type_str,
                )
                return

            # Register the sensor's EEP profile for future packet parsing
            self.register_device_profile(sensor_id_bytes, rorg_int, func_int, type_int)

            # Send discovery info with normalized integer EEP profile values
            discovery_info: DiscoveryInfo = {
                "device_id": sensor_id_bytes,
                "eep_profile": {
                    "rorg": rorg_int,
                    "rorg_func": func_int,
                    "rorg_type": type_int,
                    "manufacturer": None,
                },
            }
            # Schedule discovery signal in event loop thread-safely
            self.hass.loop.call_soon_threadsafe(
                lambda: dispatcher_send(
                    self.hass, SIGNAL_DISCOVER_DEVICE, discovery_info
                )
            )

        except (KeyError, AttributeError, TypeError):
            _LOGGER.error("Error parsing Ventilairsec sensor data CMD=8")


def detect():
    """Return a list of candidate paths for USB EnOcean dongles.

    This method is currently a bit simplistic, it may need to be
    improved to support more configurations and OS.
    """
    globs_to_test = ["/dev/tty*FTOA2PV*", "/dev/serial/by-id/*EnOcean*"]
    found_paths = []
    for current_glob in globs_to_test:
        found_paths.extend(glob.glob(current_glob))

    return found_paths


def validate_path(path: str):
    """Return True if the provided path points to a valid serial port, False otherwise."""
    try:
        # Creating the serial communicator will raise an exception
        # if it cannot connect
        SerialCommunicator(port=path)
    except serial.SerialException as exception:
        _LOGGER.warning("Dongle path %s is invalid: %s", path, str(exception))
        return False
    return True
