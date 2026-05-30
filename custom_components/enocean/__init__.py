"""Support for EnOcean devices."""

from collections.abc import Awaitable, Callable
import logging
from typing import cast

from enocean.protocol.eep import get_eep as _get_eep
import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory  # type: ignore[attr-defined]
from homeassistant.helpers.typing import ConfigType

from .const import DATA_ENOCEAN, DOMAIN, ENOCEAN_DONGLE, PLATFORMS
from .dongle import SIGNAL_DISCOVER_DEVICE, EnOceanDongle
from .eep_devices import get_entities_for_device
from .entity import format_device_id_hex, format_device_id_hex_underscore
from .types import DiscoveryInfo, EEPEntityDef, EepProfile, EntityType

# Registry for platform entity add callbacks
PLATFORM_ADD_ENTITIES_CALLBACKS: dict[
    str, Callable[[list[int], list[EEPEntityDef], int, int, int], Awaitable[None]]
] = {}

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Required(CONF_DEVICE): cv.string})},
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the EnOcean component."""
    # support for text-based configuration (legacy)
    if DOMAIN not in config:
        return True

    enocean_config = config[DOMAIN]

    if hass.config_entries.async_entries(DOMAIN):
        # We can only have one dongle. If there is already one in the config,
        # there is no need to import the yaml based config.
        return True

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=enocean_config
        )
    )

    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Set up the EnOcean dongle (following ZHA pattern)."""
    enocean_data = hass.data.setdefault(DATA_ENOCEAN, {})
    enocean_data["platform_callbacks"] = {}

    # Only the dongle config entry is supported
    if CONF_DEVICE not in config_entry.data:
        _LOGGER.warning("Config entry has no device path, skipping setup")
        return False

    # Initialize runtime_data if needed for device profile persistence
    if not hasattr(config_entry, "runtime_data") or config_entry.runtime_data is None:
        config_entry.runtime_data = {}

    usb_dongle = EnOceanDongle(hass, config_entry.data[CONF_DEVICE], config_entry)
    # Start dongle communication but don't load persisted profiles yet
    await usb_dongle.async_setup(load_profiles=False)
    enocean_data[ENOCEAN_DONGLE] = usb_dongle

    # Set up platforms using modern config entry approach
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    # Set up device discovery listener (ZHA-style: add to device registry)
    async def async_device_discovered(discovery_info: DiscoveryInfo) -> None:
        """Handle device discovery signal from dongle - add device to registry.

        This async function processes device discovery and runs blocking EEP
        file operations in the executor rather than inside the event loop.
        """
        device_id = discovery_info["device_id"]
        # Convert device_id from list[int] to hex:hex:hex:hex format for device registry
        device_id_hex_underscore = format_device_id_hex_underscore(device_id)

        # Narrow the TypedDict to a local variable so types are preserved
        eep_profile: EepProfile = discovery_info["eep_profile"]
        # Minimal runtime guard: require that EEP profile fields are integers
        # as defined by the TypedDict `EepProfile`. The dongle should send
        # normalized integer values; if it's not implemented correctly, skip
        # discovery and log a concise warning rather than attempting complex
        # coercions here.
        _eep = None
        rorg = eep_profile.get("rorg")
        rorg_func = eep_profile.get("rorg_func", eep_profile.get("func"))
        rorg_type = eep_profile.get("rorg_type", eep_profile.get("type"))

        if not (
            isinstance(rorg, int)
            and isinstance(rorg_func, int)
            and isinstance(rorg_type, int)
        ):
            _LOGGER.warning(
                "Invalid EEP profile for device %s: expected integer fields in EepProfile, got %s",
                format_device_id_hex(device_id),
                eep_profile,
            )
            return

        device_registry = dr.async_get(hass)
        # Check if device already exists in device registry
        existing_device = device_registry.async_get_device(
            identifiers={(DOMAIN, device_id_hex_underscore)}
        )

        # Check if device already has entities created
        has_entities = usb_dongle.has_device_entities(device_id)

        # Only validate EEP profile and create entities if device doesn't have entities yet
        if not has_entities:
            # If python-enocean's EEP parser is available, check that the
            # (RORG, FUNC, TYPE) tuple exists in the EEP database. The
            # EEP.find_profile method will normalize multi-byte RORGs.
            # If the python-enocean EEP parser is available, try to resolve the
            # (RORG, FUNC, TYPE) tuple to a known profile. If not available or
            # lookup fails, fall back to the simpler func presence check below.
            # Defer any potentially blocking EEP parsing to the executor
            # to avoid opening files in the event loop.
            profile = None

            if _get_eep is not None:
                try:
                    # Get cached EEP parser instance in executor (it may open files)
                    _eep = await hass.async_add_executor_job(_get_eep)

                    # Try to resolve the full <profile> element from the parser's
                    # internal telegrams mapping. Calling into the mapping is
                    # potentially file/CPU-bound, so run in the executor.
                    def _get_full_profile(
                        eep, rorg: int, rorg_func: int, ptype: int
                    ) -> dict | None:
                        try:
                            return eep.telegrams[rorg][rorg_func][ptype]
                        except (
                            FileNotFoundError,
                            OSError,
                            ValueError,
                            KeyError,
                        ) as err:
                            _LOGGER.warning(
                                "Error getting eep profile for device %s rorg=0x%02x func=0x%02x type=0x%02x: %s",
                                eep,
                                rorg,
                                rorg_func,
                                ptype,
                                err,
                            )
                            return None

                    profile = await hass.async_add_executor_job(
                        _get_full_profile, _eep, rorg, rorg_func, rorg_type
                    )

                    # Fall back to the library's find_profile if direct lookup
                    # did not return a full <profile> element (older library
                    # behaviour may require it).
                    if profile is None:
                        profile = await hass.async_add_executor_job(
                            _eep.find_profile, None, rorg, rorg_func, rorg_type
                        )
                except (ValueError, TypeError, LookupError) as err:
                    _LOGGER.debug("EEP profile lookup failed: %s", err)
                    profile = None

            if profile is None:
                # If the EEP parser is not available, require func to be present
                # before accepting the device; otherwise log a warning and skip.
                if _eep is None:
                    _LOGGER.error(
                        "Unable to verify EEP profile for device %s as python-enocean EEP parser is not installed; "
                    )
                else:
                    _LOGGER.warning(
                        "Device %s has unknown EEP profile (rorg=0x%02x func=0x%02x type=0x%02x), skipping integration",
                        format_device_id_hex(device_id),
                        rorg,
                        rorg_func,
                        rorg_type,
                    )
                    return

            _LOGGER.info(
                "Discovered EnOcean device: %s rorg=0x%02x, func=0x%02x type=0x%02x",
                format_device_id_hex(device_id),
                rorg,
                rorg_func,
                rorg_type,
            )
        else:
            # Device already has entities, just register profile and return
            usb_dongle.register_device_profile(device_id, rorg, rorg_func, rorg_type)
            return

        # Load and populate entities from the resolved EEP profile with YAML mapping overlays
        # This only happens for newly discovered devices
        entities = await hass.async_add_executor_job(
            get_entities_for_device,
            cast(
                EepProfile,
                {
                    "rorg": rorg,
                    "rorg_func": rorg_func,
                    "rorg_type": rorg_type,
                    "manufacturer": eep_profile.get("manufacturer"),
                },
            ),
        )

        if entities is None:
            entities = []
            _LOGGER.warning(
                "No entities created except signal strength for device %s as EEP profile %02x-%02x-%02x could not be loaded",
                format_device_id_hex(device_id),
                rorg,
                rorg_func,
                rorg_type,
            )
        elif not entities:
            _LOGGER.warning(
                "Learned profile for device %s has no entity mapping (rorg=0x%02x func=0x%02x type=0x%02x)",
                format_device_id_hex(device_id),
                rorg,
                rorg_func,
                rorg_type,
            )
        entities.append(
            EEPEntityDef(
                description="Signal strength",
                rorg=rorg,
                rorg_func=rorg_func,
                rorg_type=rorg_type,
                data_field="RSSI",
                entity_type=EntityType.SENSOR,
                device_class=SensorDeviceClass.SIGNAL_STRENGTH,
                entity_category=EntityCategory.DIAGNOSTIC,
                state_class=SensorStateClass.MEASUREMENT,
                unit="dBm",
            )
        )
        # Register device EEP profile with dongle for systematic packet parsing
        usb_dongle.register_device_profile(device_id, rorg, rorg_func, rorg_type)

        # Create or update device in registry
        if not existing_device:
            device_registry.async_get_or_create(
                config_entry_id=config_entry.entry_id,
                identifiers={(DOMAIN, format_device_id_hex_underscore(device_id))},
                name=f"{DOMAIN} {format_device_id_hex(device_id)}",
                manufacturer="EnOcean",
                model=f"0x{rorg:02x} (func=0x{(rorg_func or 0):02x}, type=0x{rorg_type:02x})",
            )

        def _ent_val(ent, key):
            if isinstance(ent, dict):
                return ent.get(key)
            return getattr(ent, key, None)

        # Call registered platform callbacks directly to add entities
        platform_callbacks = enocean_data.get("platform_callbacks", {})
        for platform_name, callback in platform_callbacks.items():
            try:
                await callback(
                    device_id,
                    entities,
                    rorg,
                    rorg_func,
                    rorg_type,
                )
            except (
                TimeoutError,
                RuntimeError,
                ValueError,
                TypeError,
                LookupError,
                OSError,
            ):
                _LOGGER.exception(
                    "Error calling platform callback %s for device %s",
                    platform_name,
                    format_device_id_hex(device_id),
                )

        # Mark device as having entities to prevent repeated rediscovery
        usb_dongle.mark_device_has_entities(device_id)

    # Register listener for device discovery signals
    async def _handle_device_discovered(discovery_info: DiscoveryInfo) -> None:
        """Handle device discovery signal from dispatcher."""
        await async_device_discovered(discovery_info)

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_DISCOVER_DEVICE, _handle_device_discovered
        )
    )

    # Load persisted device profiles AFTER platforms and discovery listener are set up
    # This ensures platform callbacks and discovery handlers are registered before discovery signals are dispatched
    await usb_dongle.async_load_device_profiles()

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload EnOcean config entry."""

    enocean_data = hass.data.get(DATA_ENOCEAN, {})

    enocean_dongle = enocean_data.get(ENOCEAN_DONGLE)
    if enocean_dongle:
        enocean_dongle.unload()

    sensor_manager = enocean_data.get("sensor_manager")
    if sensor_manager:
        await sensor_manager.async_unload()

    hass.data.pop(DATA_ENOCEAN, None)

    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Remove a device from the EnOcean integration.

    This allows users to delete discovered EnOcean devices from the UI.
    The device will be re-discovered if it sends another packet.
    """
    # Check if device belongs to this config entry
    if config_entry.entry_id not in device_entry.config_entries:
        return False

    # Check if device is an EnOcean device (has correct identifier format)
    for identifier in device_entry.identifiers:
        if identifier[0] == DOMAIN:
            _LOGGER.info(
                "Removing EnOcean device %s (%s) from integration",
                device_entry.name,
                identifier[1],
            )
            # Also remove it from the dongle's internal tracking so it can be
            # re-discovered if it sends packets in the future.
            enocean_data = hass.data.get(DATA_ENOCEAN, {})
            enocean_dongle = enocean_data.get(ENOCEAN_DONGLE)
            if enocean_dongle is not None:
                try:
                    enocean_dongle.remove_entity_for_device(identifier[1])
                except Exception:
                    _LOGGER.exception(
                        "Error removing device %s from dongle internal tracking",
                        identifier[1],
                    )
            # Allow removal - device will be re-discovered if it sends packets
            return True

    return False
