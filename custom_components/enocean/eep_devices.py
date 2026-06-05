"""EEP device entity definitions with dynamic EEP-based discovery.

Simple, clean architecture:
- Load EEP profile and YAML mapping once per device
- Single public function: get_entities_for_device(eep_profile)
- Returns list of EEPEntityDef ready for entity creation
"""

from __future__ import annotations

# Some functions in this module perform layered configuration parsing which
# necessarily creates deep nesting. The parsing logic is complex and hard to
# simplify without reducing readability; explicitly skip pylint for this file
# to avoid noisy R1702 complaints while other linters still apply.
# pylint: skip-file
from collections.abc import Iterable
import contextlib
from functools import cache
import importlib
import logging
from pathlib import Path
from typing import Any

import yaml

from homeassistant.components.sensor import SensorStateClass
import homeassistant.const as hac_const
from homeassistant.const import PERCENTAGE
from homeassistant.helpers.entity import EntityCategory  # type: ignore[attr-defined]

from .types import EEPEntityDef, EepProfile, EntityType

_LOGGER = logging.getLogger(__name__)


def _convert_rps_to_events(entities: list[EEPEntityDef]) -> list[EEPEntityDef]:
    """Convert RPS enum fields to event entities with proper event types.
    
    For F6 profiles, enum fields like R1, R2, WIN, PB define button actions.
    Convert these to event entities where enum_items become event_types.
    Skip auxiliary fields like EB (Energy bow) and SA (2nd action) which are flags.
    """
    # Auxiliary fields that should not be event entities
    SKIP_FIELDS = {"EB", "SA", "T21", "NU"}
    
    event_entities = []
    
    for entity in entities:
        # For F6 RPS profiles, convert enum fields to events (except auxiliary fields)
        if entity.rorg == 0xF6 and entity.enum_items and entity.data_field not in SKIP_FIELDS:
            # Create an event entity with enum descriptions as event types
            event_types = [item["description"] for item in entity.enum_items]
            if event_types:
                event_entities.append(
                    EEPEntityDef(
                        description=f"{entity.description} events",
                        rorg=entity.rorg,
                        rorg_func=entity.rorg_func,
                        rorg_type=entity.rorg_type,
                        data_field=entity.data_field,
                        entity_type=EntityType.EVENT,
                        icon="mdi:gesture-tap-button",
                        enum_options=event_types,
                        enum_items=entity.enum_items,
                        offset=entity.offset,
                    )
                )
    
    return event_entities if event_entities else entities


@cache
def _load_eep_mapping() -> dict:
    """Load EEP-to-platform mapping from YAML file.

    Returns: Dict with structure {rorg: {func: {type: {entities: [...]}}}}
    """
    if yaml is None:
        _LOGGER.warning("PyYAML not available; EEP mapping will not be loaded")
        return {}

    mapping_path = Path(__file__).parent / "eep_platform_mapping.yaml"
    if not mapping_path.exists():
        _LOGGER.warning("EEP mapping file not found at %s", mapping_path)
        return {}

    try:
        with open(mapping_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            mapping = {
                k: v
                for k, v in data.items()
                if k not in ["auto_detect_remove", "common", "system"]
            }
            _LOGGER.debug("Loaded EEP mapping with %d RORG profiles", len(mapping))
            return mapping
    except OSError as err:
        _LOGGER.error("Failed to load EEP mapping file: %s", err)
        return {}
    except yaml.YAMLError as err:
        _LOGGER.error("Failed to parse EEP mapping YAML: %s", err)
        return {}


def _load_eep_profile(eep_profile: EepProfile):
    """Load EEP profile from python-enocean library.

    Args:
        eep_profile: EepProfile with rorg, func, type, manufacturer

    Returns: Parsed EEP profile object or None if not found.
    """
    try:
        eep_module = importlib.import_module("enocean.protocol.eep")
        eep_instance = getattr(eep_module, "_eep_instance", None)
        if eep_instance is None or not getattr(eep_instance, "init_ok", False):
            _LOGGER.debug("EEP parser not initialized")
            profile = None
        else:
            profile = eep_instance.find_profile(
                eep_profile.get("manufacturer"),
                eep_profile.get("rorg"),
                eep_profile.get("rorg_func"),
                eep_profile.get("rorg_type"),
            )
            if profile is None:
                _LOGGER.debug(
                    "EEP profile not found for RORG=%s FUNC=%s TYPE=%s",
                    hex(int(eep_profile.get("rorg") or 0)),
                    hex(int(eep_profile.get("rorg_func") or 0)),
                    hex(int(eep_profile.get("rorg_type") or 0)),
                )
    except (ImportError, ModuleNotFoundError, AttributeError) as err:
        # Catch only expected import/attribute errors when python-enocean is not available
        _LOGGER.debug("Failed to load EEP profile: %s", err)
        return None
    else:
        return profile


def _extract_eep_fields(
    profile, rorg: int, rorg_func: int, rorg_type: int
) -> list[EEPEntityDef]:
    """Extract field definitions from EEP profile object.

    Args:
        profile: BeautifulSoup element representing an EEP profile
        rorg: RORG value of the EEP profile
        rorg_func: RORG function value of the EEP profile
        rorg_type: RORG type value of the EEP profile

    Returns: List of field dicts with shortcut, description, type, unit, min/max, items.
    """
    fields = []

    # Check for multi-command profiles (contain <data command="..."> elements)
    # If the profile itself is a <data> element, we need to get its siblings
    if profile.name == "data" and profile.parent:
        # Profile is already a <data> element, get all sibling <data> elements from parent
        data_commands = profile.parent.find_all("data")
    else:
        # Profile is a <profile> element, find nested <data> elements
        data_commands = profile.find_all("data")

    containers = data_commands if data_commands else [profile]

    for container in containers:  # pylint: disable=too-many-nested-blocks
        # Extract value, enum, and boolean fields
        for field_type in ("value", "enum", "boolean"):
            for element in container.find_all(field_type, recursive=False):
                shortcut = element.get("shortcut")
                if not shortcut or shortcut == "CMD":
                    continue

                description = element.get("description") or shortcut
                unit = element.get("unit")

                # Extract min/max from <scale> (preferred) or <range>
                min_value = None
                max_value = None

                scale_el = element.find("scale")
                if scale_el:
                    for bound_type, val_var in (
                        ("min", "min_value"),
                        ("max", "max_value"),
                    ):
                        bound_el = scale_el.find(bound_type)
                        if bound_el and bound_el.text:
                            with contextlib.suppress(ValueError, TypeError):
                                locals()[val_var] = float(bound_el.text.strip())

                if min_value is None or max_value is None:
                    range_el = element.find("range")
                    if range_el:
                        for bound_type in ("min", "max"):
                            bound_el = range_el.find(bound_type)
                            if bound_el and bound_el.text:
                                try:
                                    val = float(bound_el.text.strip())
                                    if bound_type == "min" and min_value is None:
                                        min_value = val
                                    elif bound_type == "max" and max_value is None:
                                        max_value = val
                                except (ValueError, TypeError):
                                    pass

                # Extract enum items (both <item> and <rangeitem>)
                items = None
                if field_type == "enum":
                    item_list = []

                    # Process regular <item> elements
                    for item in element.find_all("item"):
                        raw_val = item.get("value")
                        try:
                            val = int(str(raw_val), 0)
                        except (ValueError, TypeError):
                            val = raw_val
                        item_list.append(
                            {"value": val, "description": item.get("description", "")}
                        )

                    # Process <rangeitem> elements (expand range into individual items)
                    for rangeitem in element.find_all("rangeitem"):
                        try:
                            start = int(rangeitem.get("start", 0))
                            end = int(rangeitem.get("end", 0))
                            desc_template = rangeitem.get("description", "{value}")

                            # Expand range into individual items
                            for val in range(start, end + 1):
                                # Replace {value} placeholder in description
                                desc = desc_template.replace("{value}", str(val))
                                item_list.append({"value": val, "description": desc})
                        except (ValueError, TypeError, AttributeError):
                            # Skip invalid rangeitems
                            continue

                    if item_list:
                        items = item_list

                offset = None
                offset_el = element.find("offset")
                if offset_el and offset_el.text:
                    with contextlib.suppress(ValueError, TypeError):
                        offset = int(offset_el.text.strip(), 0)
                size_el = element.find("size")
                if size_el and size_el.text:
                    with contextlib.suppress(ValueError, TypeError):
                        size = int(size_el.text.strip(), 0)
                entity_def = EEPEntityDef(
                    description=description,
                    rorg=rorg,
                    rorg_func=rorg_func,
                    rorg_type=rorg_type,
                    data_field=shortcut,
                    unit=_normalize_unit(unit),
                    device_class=None,
                    entity_type=_classify_entity_type(
                        shortcut, description, field_type, items, min_value, max_value
                    ),
                    min_value=min_value,
                    max_value=max_value,
                    enum_options=(
                        [it["description"] for it in (items or [])] if items else None
                    ),
                    enum_items=items,  # Store full items with values and descriptions
                    offset=offset,
                )
                # Apply smart auto-detection for device properties
                _auto_detect_entity_properties(entity_def)
                fields.append(entity_def)

    return fields


def _auto_detect_entity_properties(entity_def: EEPEntityDef) -> None:
    """Auto-detect and set entity properties based on description and data_field.

    Detects device_class, icon, state_class, entity_category based on patterns
    in the description and shortcut. Takes inspiration from common patterns.

    Args:
        entity_def: Entity definition to enhance with detected properties
    """
    desc_lower = (entity_def.description or "").lower()
    shortcut_upper = (entity_def.data_field or "").upper()

    def _set_if_missing(**kwargs):
        if "device_class" in kwargs and not entity_def.device_class:
            entity_def.device_class = kwargs["device_class"]
        if "unit" in kwargs and not entity_def.unit:
            entity_def.unit = kwargs["unit"]
        if "icon" in kwargs and not entity_def.icon:
            entity_def.icon = kwargs["icon"]
        if "state_class" in kwargs and not entity_def.state_class:
            entity_def.state_class = kwargs["state_class"]
        if "entity_category" in kwargs and not entity_def.entity_category:
            entity_def.entity_category = kwargs["entity_category"]

    detectors = [
        (
            lambda: any(
                k in desc_lower
                for k in ("temperature", "température", "temp", "temperatur")
            )
            or "TEMP" in shortcut_upper,
            {
                "device_class": "temperature",
                "unit": "°C",
                "icon": "mdi:thermometer",
                "state_class": SensorStateClass.MEASUREMENT,
            },
        ),
        (
            lambda: any(
                k in desc_lower
                for k in ("humidity", "humidité", "humid", "feuchtigkeit")
            )
            or "HUM" in shortcut_upper,
            {
                "device_class": "humidity",
                "unit": "%",
                "icon": "mdi:water-percent",
                "state_class": SensorStateClass.MEASUREMENT,
            },
        ),
        (
            lambda: any(k in desc_lower for k in ("battery", "batterie", "batt"))
            or "BATT" in shortcut_upper,
            {
                "device_class": "battery",
                "unit": "%",
                "icon": "mdi:battery",
                "entity_category": EntityCategory.DIAGNOSTIC,
                "state_class": SensorStateClass.MEASUREMENT,
            },
        ),
        (
            lambda: any(k in desc_lower for k in ("power", "puissance", "leistung"))
            or "POW" in shortcut_upper
            or "PWR" in shortcut_upper,
            {
                "device_class": "power",
                "unit": "W",
                "icon": "mdi:lightning-bolt",
                "state_class": SensorStateClass.MEASUREMENT,
            },
        ),
        (
            lambda: any(k in desc_lower for k in ("energy", "energie", "énergie"))
            or "ENERGY" in shortcut_upper,
            {
                "device_class": "energy",
                "unit": "Wh",
                "icon": "mdi:lightning-bolt",
                "state_class": SensorStateClass.TOTAL_INCREASING,
            },
        ),
        (
            lambda: any(k in desc_lower for k in ("voltage", "tension", "spannung"))
            or "VOLT" in shortcut_upper
            or shortcut_upper == "U",
            {
                "device_class": "voltage",
                "unit": "V",
                "icon": "mdi:flash",
                "state_class": SensorStateClass.MEASUREMENT,
            },
        ),
        (
            lambda: any(k in desc_lower for k in ("current", "courant", "strom"))
            or "AMP" in shortcut_upper
            or shortcut_upper == "I",
            {
                "device_class": "current",
                "unit": "A",
                "icon": "mdi:current-ac",
                "state_class": SensorStateClass.MEASUREMENT,
            },
        ),
        (
            lambda: any(k in desc_lower for k in ("illuminance", "lux", "light"))
            or "LUX" in shortcut_upper,
            {
                "device_class": "illuminance",
                "unit": "lx",
                "icon": "mdi:brightness-5",
                "state_class": SensorStateClass.MEASUREMENT,
            },
        ),
        (
            lambda: any(k in desc_lower for k in ("motion", "presence", "occupancy"))
            or any(s in shortcut_upper for s in ("PIR", "MOT")),
            {"icon": "mdi:run"},
        ),
        (
            lambda: any(k in desc_lower for k in ("smoke", "fumée", "rauch"))
            or "SMOKE" in shortcut_upper,
            {"icon": "mdi:smoke"},
        ),
        (
            lambda: any(k in desc_lower for k in ("co2", "co₂", "carbon dioxide"))
            or "CO2" in shortcut_upper,
            {
                "unit": "ppm",
                "icon": "mdi:molecule-co2",
                "state_class": SensorStateClass.MEASUREMENT,
            },
        ),
    ]

    for predicate, props in detectors:
        try:
            if predicate():
                _set_if_missing(**props)
                break
        except (AttributeError, TypeError, ValueError):
            # Be defensive: catch only expected errors from detectors so auto-detection
            # continues without masking unrelated exceptions
            continue


def _normalize_unit(unit: str | None) -> str | None:
    """Normalize common unit strings to Home Assistant constants.

    Try to dynamically match against all UnitOf* enums in homeassistant.const.
    Fall back to a small set of common synonyms for short forms.
    """
    if not unit:
        return None

    raw = str(unit).strip()
    lower = raw.lower()

    # Common percent synonyms
    if lower in ("%", "percent", "percentage"):
        return PERCENTAGE

    # Small synonyms for short forms that may not directly match enum values
    SYNONYMS = {
        "c": "°c",
        "°c": "°c",
        "celsius": "celsius",
        "f": "°f",
        "°f": "°f",
        "fahrenheit": "fahrenheit",
        "w": "w",
        "watt": "w",
        "watts": "w",
        "wh": "wh",
        "kwh": "kwh",
        "v": "v",
        "a": "a",
        "lx": "lx",
        "ppm": "ppm",
    }
    if lower in SYNONYMS:
        lower = SYNONYMS[lower]

    def _norm_text(s: str) -> str:
        return s.lower().replace("°", "").strip()

    # Iterate all UnitOf* attributes in homeassistant.const and try to match
    for attr in dir(hac_const):
        if not attr.startswith("UnitOf"):
            continue
        unit_cls = getattr(hac_const, attr)
        # Enum-like classes provide __members__; otherwise inspect uppercase attrs
        members: Iterable[Any] = ()
        if hasattr(unit_cls, "__members__"):
            members = unit_cls.__members__.values()
        else:
            members = (
                getattr(unit_cls, name) for name in dir(unit_cls) if name.isupper()
            )

        for member in members:
            try:
                val = getattr(member, "value", member)
                cand = str(val).lower()
                if lower == cand or lower == _norm_text(cand):
                    return val
                # Also try matching by enum/member name if available
                name = getattr(member, "name", None)
                if name:
                    if lower == name.lower() or lower == _norm_text(name):
                        return val
            except (AttributeError, TypeError, ValueError):
                # Defensive: skip problematic members that don't match the expected
                # interface or provide non-string values
                continue

    # No dynamic match: preserve original formatting
    return raw


def _classify_entity_type(
    shortcut: str | None,
    description: str | None,
    field_type: str,
    items: list[dict] | None,
    min_value: float | None = None,
    max_value: float | None = None,
) -> EntityType:
    """Classify field to Home Assistant entity type.

    Returns one of: EntityType.SENSOR, EntityType.BINARY_SENSOR, EntityType.SELECT,
    EntityType.LIGHT, EntityType.BUTTON, EntityType.NUMBER.
    """

    sc = (shortcut or "").upper()
    desc = (description or "").lower()

    # Booleans -> binary_sensor
    if field_type == "boolean":
        return EntityType.BINARY_SENSOR

    # Enums with items -> select or binary_sensor (highest priority)
    # This includes fields with <item> or <rangeitem> elements
    if items and len(items) > 0:
        vals = {int(it["value"]) if isinstance(it["value"], int) else 0 for it in items}
        if vals == {0, 1}:
            return EntityType.BINARY_SENSOR
        if sc.startswith("R") and len(vals) >= 3:
            return EntityType.BUTTON
        if len(vals) > 2:
            return EntityType.SELECT
        return EntityType.SENSOR

    # Known shortcuts -> binary_sensor
    if sc in {"WAS", "SMO", "CO"}:
        return EntityType.BINARY_SENSOR

    if sc == "WIN":
        return EntityType.SELECT

    # Lights/dimmers
    if any(k in sc for k in ("EDIM", "DIM", "BRI", "BRIGHT", "DMD")) or any(
        w in desc for w in ("dimm", "brightness")
    ):
        return EntityType.LIGHT

    # Commands -> button
    if sc == "CMD" or "command" in desc:
        return EntityType.BUTTON

    # Value fields with finite range -> number (configurable parameters)
    # Typical pattern: fields like volume, speed, setpoints with defined min/max
    if field_type == "value" and min_value is not None and max_value is not None:
        # Heuristics: if the range is reasonable for configuration (not just sensor readings)
        # Check if range suggests a settable parameter rather than a measurement
        range_size = max_value - min_value
        # Avoid very large ranges that are likely just sensor scales
        if 0 < range_size <= 10000:
            # Additional check: certain keywords suggest configurable values
            config_keywords = [
                "volume",
                "speed",
                "setpoint",
                "target",
                "position",
                "bypass",
                "ventil",
                "vitesse",
                "débit",
                "consigne",
            ]
            if any(kw in desc for kw in config_keywords) or any(
                kw in sc for kw in ["VIT", "VOL", "POS", "BY", "VVENT"]
            ):
                return EntityType.NUMBER

    # Default: sensor
    return EntityType.SENSOR


def get_entities_for_device(eep_profile: EepProfile) -> list[EEPEntityDef]:
    """Build entity definitions for a device from EEP profile, overlaid with YAML mapping.

    This is the single public API function. It handles:
    1. Load EEP profile (source of truth for all available fields)
    2. Build entities from EEP
    3. Overlay YAML mapping overrides for customization
    4. Return merged list

    Args:
        eep_profile: EepProfile with rorg, func, type, manufacturer

    Returns: List of EEPEntityDef instances for entity creation.
    """
    rorg = eep_profile["rorg"]
    rorg_func = eep_profile["rorg_func"]
    rorg_type = eep_profile["rorg_type"]

    if any(v is None for v in (rorg, rorg_func, rorg_type)):
        _LOGGER.warning("Invalid EEP profile: missing rorg, func, or type")
        return []

    # Load EEP profile (source of truth)
    profile = _load_eep_profile(eep_profile)
    if not profile:
        _LOGGER.warning(
            "No EEP profile found for RORG=%s FUNC=%s TYPE=%s",
            hex(rorg),
            hex(rorg_func),
            hex(rorg_type),
        )
        return []

    eep_fields = _extract_eep_fields(profile, rorg, rorg_func, rorg_type)
    if not eep_fields:
        _LOGGER.warning(
            "EEP profile has no fields for RORG=%s FUNC=%s TYPE=%s",
            hex(rorg),
            hex(rorg_func),
            hex(rorg_type),
        )
        return []

    entities = eep_fields

    # Overlay YAML mapping overrides
    mapping = _load_eep_mapping()
    mapping_applied = False
    rorg_entry = mapping.get(rorg)
    if rorg_entry:
        func_entry = rorg_entry.get(rorg_func)
        if func_entry:
            type_entry = func_entry.get(rorg_type)
            if type_entry and type_entry.get("entities"):
                mapping_applied = True
                entities = _overlay_mapping_overrides(
                    entities, type_entry, rorg, rorg_func, rorg_type
                )

    # For F6 RPS profiles, convert enum fields to event entities
    if rorg == 0xF6:
        entities = _convert_rps_to_events(entities)

    _LOGGER.debug(
        "Built %d entities from EEP profile with mapping overrides (RORG=%s FUNC=%s TYPE=%s)",
        len(entities),
        hex(rorg),
        hex(rorg_func),
        hex(rorg_type),
    )

    return entities


def _expand_channel_entities(entity_def: dict, eep_entities: list[EEPEntityDef]) -> list[dict]:
    """Expand an entity definition with 'channels' attribute into multiple entities.

    If entity_def has a 'channels' attribute (list of channel numbers), this
    creates one entity definition per channel with:
    - Name suffixed with '_chX'
    - Description appended with ' - Channel X'
    - {{channel}} replaced with actual channel number in templates
    - Channel number stored in config['channel'] for offset field
    - Inherits description, min_value, max_value, unit, etc. from EEP.xml if not in YAML

    Args:
        entity_def: Single entity definition from mapping YAML
        eep_entities: List of entities extracted from EEP.xml for property lookup

    Returns: List of entity definitions (single item if no channels, multiple if channels present)
    """
    channels = entity_def.get("channels")

    # No channels attribute or None - return as-is
    if channels is None:
        return [entity_def]

    # Empty list - return no entities
    if not channels:
        return []

    # Look up the base entity in EEP.xml by name for property inheritance
    base_name = entity_def.get("name", "unknown")
    eep_entity = None
    for e in eep_entities:
        if e.data_field == base_name:
            eep_entity = e
            break
    
    # Determine base description: YAML > EEP.xml > field name
    if "description" in entity_def:
        base_description = entity_def["description"]
    elif eep_entity:
        base_description = eep_entity.description
    else:
        base_description = base_name
    
    # Inherit properties from EEP entity if not specified in YAML
    # Build a config dict with EEP defaults
    inherited_config = {}
    if eep_entity:
        if eep_entity.min_value is not None:
            inherited_config["min_value"] = eep_entity.min_value
        if eep_entity.max_value is not None:
            inherited_config["max_value"] = eep_entity.max_value
        if eep_entity.unit:
            inherited_config["unit"] = eep_entity.unit
        if eep_entity.icon:
            inherited_config["icon"] = eep_entity.icon
        if eep_entity.device_class:
            inherited_config["device_class"] = eep_entity.device_class
        if eep_entity.mode:
            inherited_config["mode"] = eep_entity.mode

    # Expand into one entity per channel
    expanded = []
    
    for channel in channels:
        # Deep copy the entity definition
        import copy
        channel_entity = copy.deepcopy(entity_def)
        
        # Update name with channel suffix
        channel_entity["name"] = f"{base_name}_ch{channel}"
        
        # Update description with channel suffix
        channel_entity["description"] = f"{base_description} - Channel {channel}"
        
        # Merge inherited config from EEP with YAML config (YAML takes precedence)
        if "config" not in channel_entity:
            channel_entity["config"] = {}
        
        # Apply inherited properties first, then YAML overrides them
        for key, value in inherited_config.items():
            if key not in channel_entity["config"]:
                channel_entity["config"][key] = value
        
        # Store channel number for offset field
        channel_entity["config"]["channel"] = channel
        
        # Replace {{channel}} placeholder in templates
        config = channel_entity["config"]
        
        # Handle command_template
        if "command_template" in config:
            config["command_template"] = config["command_template"].replace(
                "{{channel}}", str(channel)
            )
        
        # Handle command_template_on
        if "command_template_on" in config:
            config["command_template_on"] = config["command_template_on"].replace(
                "{{channel}}", str(channel)
            )
        
        # Handle command_template_off
        if "command_template_off" in config:
            config["command_template_off"] = config["command_template_off"].replace(
                "{{channel}}", str(channel)
            )
        
        # Remove the channels attribute from expanded entity
        channel_entity.pop("channels", None)
        
        expanded.append(channel_entity)
    
    return expanded


def _overlay_mapping_overrides(
    eep_entities: list[EEPEntityDef],
    type_entry: dict,
    rorg: int,
    rorg_func: int,
    rorg_type: int,
) -> list[EEPEntityDef]:  # pylint: disable=too-many-nested-blocks
    """Overlay YAML mapping overrides onto EEP-derived entities.

    For each mapping entity, find the matching EEP entity by data_field name
    and update its properties with mapping values. If a mapping entity has no
    matching EEP entity, create a new entity from the mapping definition.

    Args:
        eep_entities: List of entities from EEP extraction
        type_entry: Mapping type_entry dict with 'entities' list
        rorg: RORG value for the profile
        rorg_func: FUNC value for the profile
        rorg_type: TYPE value for the profile

    Returns: Updated entities list with mapping overrides applied and new mapping-only entities added.
    """
    # Expand entities with 'channels' attribute before processing
    expanded_entities = []
    for entity_def in type_entry.get("entities", []):
        expanded = _expand_channel_entities(entity_def, eep_entities)
        expanded_entities.extend(expanded)
    
    # Build lookup of mapping entities by name (data_field)
    mapping_lookup = {}
    for entity_def in expanded_entities:
        name = entity_def.get("name")
        if name:
            mapping_lookup[name] = entity_def

    # Track which mapping entities were matched
    matched_mapping_names = set()

    # Apply overrides to matching EEP entities
    for eep_entity in eep_entities:
        data_field = eep_entity.data_field
        if data_field in mapping_lookup:
            mapping_def = mapping_lookup[data_field]
            _apply_mapping_to_entity(mapping_def, eep_entity, data_field)
            matched_mapping_names.add(data_field)

    # Create new entities for mapping-only definitions (not in EEP.xml)
    for name, mapping_def in mapping_lookup.items():
        if name not in matched_mapping_names:
            new_entity = _create_entity_from_mapping(
                mapping_def, name, rorg, rorg_func, rorg_type
            )
            if new_entity:
                eep_entities.append(new_entity)
                _LOGGER.debug(
                    "Created new entity from mapping-only definition: %s (type=%s)",
                    name,
                    new_entity.entity_type,
                )

    return eep_entities


def _create_entity_from_mapping(
    mapping_def: dict, name: str, rorg: int, rorg_func: int, rorg_type: int
) -> EEPEntityDef | None:
    """Create a new EEPEntityDef from a mapping definition alone.

    Used for entities that exist only in the YAML mapping, not in EEP.xml.
    This enables custom diagnostic entities and platform-specific features.

    Args:
        mapping_def: Single entity definition from mapping YAML
        name: Entity name (data_field)
        rorg: RORG value for the profile
        rorg_func: FUNC value for the profile
        rorg_type: TYPE value for the profile

    Returns: New EEPEntityDef instance or None if creation failed.
    """
    config = mapping_def.get("config", {})

    # Determine entity type from component field
    component_str = mapping_def.get("component", "sensor")
    try:
        entity_type = EntityType(component_str)
    except ValueError:
        _LOGGER.warning(
            "Unknown component type '%s' in mapping for %s, defaulting to sensor",
            component_str,
            name,
        )
        entity_type = EntityType.SENSOR

    # Create the entity with mapping values
    # Use explicit description from mapping, or fall back to field name
    description = mapping_def.get("description", name)
    entity = EEPEntityDef(
        description=description,
        rorg=rorg,
        rorg_func=rorg_func,
        rorg_type=rorg_type,
        data_field=name,
        entity_type=entity_type,
    )

    # Apply all config properties
    if config.get("unit"):
        entity.unit = config["unit"]

    if config.get("device_class"):
        entity.device_class = config["device_class"]

    # Support both "min"/"max" and "min_value"/"max_value"
    if config.get("min") is not None:
        with contextlib.suppress(ValueError, TypeError):
            entity.min_value = float(config["min"])
    elif config.get("min_value") is not None:
        with contextlib.suppress(ValueError, TypeError):
            entity.min_value = float(config["min_value"])

    if config.get("max") is not None:
        with contextlib.suppress(ValueError, TypeError):
            entity.max_value = float(config["max"])
    elif config.get("max_value") is not None:
        with contextlib.suppress(ValueError, TypeError):
            entity.max_value = float(config["max_value"])

    if config.get("options"):
        entity.enum_options = config["options"]

    if config.get("value_template"):
        entity.value_template = config["value_template"]

    if config.get("icon"):
        entity.icon = config["icon"]

    if config.get("state_class"):
        entity.state_class = config["state_class"]

    if config.get("entity_category"):
        val = config["entity_category"]
        resolved = _resolve_entity_category(val)
        if resolved is not None:
            entity.entity_category = resolved

    if config.get("command_template"):
        entity.command_template = config["command_template"]

    if config.get("command_template_on"):
        entity.command_template_on = config["command_template_on"]

    if config.get("command_template_off"):
        entity.command_template_off = config["command_template_off"]

    if config.get("mode"):
        entity.mode = config["mode"]

    if config.get("channel") is not None:
        with contextlib.suppress(ValueError, TypeError):
            channel_val = config["channel"]
            if isinstance(channel_val, str):
                entity.offset = int(channel_val, 0)
            else:
                entity.offset = int(channel_val)

    return entity


def _apply_mapping_to_entity(
    mapping_def: dict, eep_entity: EEPEntityDef, data_field: str
) -> None:  # pylint: disable=too-many-nested-blocks
    """Apply a single mapping override to an EEPEntityDef.

    Factored out of _overlay_mapping_overrides to reduce nesting depth for
    pylint and improve readability.
    """
    config = mapping_def.get("config", {})

    _LOGGER.debug(
        "Applying mapping override for %s: component=%s -> %s",
        data_field,
        mapping_def.get("component"),
        eep_entity.entity_type,
    )

    # Override description if provided in mapping
    if mapping_def.get("description"):
        eep_entity.description = mapping_def["description"]

    # Override component/entity type when provided
    if mapping_def.get("component"):
        component_str = mapping_def["component"]
        try:
            eep_entity.entity_type = EntityType(component_str)
        except ValueError:
            _LOGGER.warning(
                "Unknown component type '%s' in mapping for %s, keeping auto-classified %s",
                component_str,
                data_field,
                eep_entity.entity_type,
            )

    if config.get("unit"):
        eep_entity.unit = config["unit"]

    if config.get("device_class"):
        eep_entity.device_class = config["device_class"]

    if config.get("min") is not None:
        with contextlib.suppress(ValueError, TypeError):
            eep_entity.min_value = float(config["min"])

    if config.get("max") is not None:
        with contextlib.suppress(ValueError, TypeError):
            eep_entity.max_value = float(config["max"])

    if config.get("options"):
        eep_entity.enum_options = config["options"]

    if config.get("value_template"):
        eep_entity.value_template = config["value_template"]

    if config.get("icon"):
        eep_entity.icon = config["icon"]

    if config.get("state_class"):
        eep_entity.state_class = config["state_class"]

    if config.get("entity_category"):
        val = config["entity_category"]
        resolved = _resolve_entity_category(val)
        if resolved is not None:
            eep_entity.entity_category = resolved
        else:
            _LOGGER.warning(
                "Invalid entity_category '%s' in mapping for %s; must be one of: %s",
                val,
                data_field,
                ", ".join(e.value for e in EntityCategory),
            )

    if config.get("command_template"):
        eep_entity.command_template = config["command_template"]

    if config.get("command_template_on"):
        eep_entity.command_template_on = config["command_template_on"]

    if config.get("command_template_off"):
        eep_entity.command_template_off = config["command_template_off"]

    if config.get("mode"):
        eep_entity.mode = config["mode"]

    if config.get("channel") is not None:
        with contextlib.suppress(ValueError, TypeError):
            channel_val = config["channel"]
            if isinstance(channel_val, str):
                eep_entity.offset = int(channel_val, 0)
            else:
                eep_entity.offset = int(channel_val)


def _resolve_entity_category(val: object) -> EntityCategory | None:
    """Resolve an EntityCategory from a mapping value.

    Accepts either an EntityCategory instance or a string matching either the
    enum value or member name (case-insensitive). Returns None on failure.
    """
    if isinstance(val, EntityCategory):
        return val
    if isinstance(val, str):
        try:
            return EntityCategory(val)
        except ValueError:
            try:
                return EntityCategory[val.upper()]
            except KeyError:
                return None
    return None
