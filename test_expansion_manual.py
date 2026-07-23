#!/usr/bin/env python3
"""Quick manual test for channel expansion with EEP property inheritance."""

import sys
sys.path.insert(0, '/config/ha-enocean-repo/custom_components/enocean')

from eep_devices import _expand_channel_entities
from types import EEPEntityDef, EntityType

# Create a mock EEP entity for DT1
eep_dt1 = EEPEntityDef(
    description="Dim timer 1",
    rorg=0xD2,
    rorg_func=0x01,
    rorg_type=0x12,
    data_field="DT1",
    entity_type=EntityType.NUMBER,
    min_value=0,
    max_value=255,
    unit="0.5s"
)

eep_entities = [eep_dt1]

# Test 1: Without YAML description (should use EEP.xml description)
yaml_def_no_desc = {
    "component": "number",
    "name": "DT1",
    "channels": [0, 1],
    "config": {
        "entity_category": "config",
        "command_template": '{"CMD": 2, "IO": {{channel}}, "DT1": {{value}}}'
    }
}

print("Test 1: Without YAML description (should inherit from EEP.xml)")
result = _expand_channel_entities(yaml_def_no_desc, eep_entities)
for entity in result:
    print(f"  Name: {entity['name']}")
    print(f"  Description: {entity['description']}")
    print(f"  Config: {entity['config']}")
    print()

# Test 2: With YAML description (should override EEP.xml)
yaml_def_with_desc = {
    "component": "number",
    "name": "DT1",
    "description": "Custom dim timer",
    "channels": [0, 1],
    "config": {
        "entity_category": "config",
        "command_template": '{"CMD": 2, "IO": {{channel}}, "DT1": {{value}}}'
    }
}

print("Test 2: With YAML description (should override EEP.xml)")
result = _expand_channel_entities(yaml_def_with_desc, eep_entities)
for entity in result:
    print(f"  Name: {entity['name']}")
    print(f"  Description: {entity['description']}")
    print(f"  Config: {entity['config']}")
    print()

# Test 3: Field not in EEP (should fall back to field name)
yaml_def_no_eep = {
    "component": "light",
    "name": "channel",
    "description": "Light channel",
    "channels": [0, 1],
    "config": {
        "command_template_on": '{"CMD": 1, "IO": {{channel}}, "OV": 100}'
    }
}

print("Test 3: Field not in EEP (uses YAML description)")
result = _expand_channel_entities(yaml_def_no_eep, eep_entities)
for entity in result:
    print(f"  Name: {entity['name']}")
    print(f"  Description: {entity['description']}")
    print(f"  Config: {entity['config']}")
    print()

print("All tests completed!")
