#!/usr/bin/env python3
"""Manual test for channel expansion enum options inheritance."""

import sys
sys.path.insert(0, '/config/ha-enocean-repo/custom_components/enocean')

from eep_devices import _expand_channel_entities
from types import EEPEntityDef, EntityType

print("=" * 80)
print("TEST 1: Select entity with channels should inherit enum_options from EEP.xml")
print("=" * 80)

# Create a mock EEP entity for EBM with enum options
eep_ebm = EEPEntityDef(
    description="External Switch/Push Button",
    rorg=0xD2,
    rorg_func=0x01,
    rorg_type=0x12,
    data_field="EBM",
    entity_type=EntityType.SELECT,
    enum_options=["Not applicable", "External Switch", "External Push Button", "Auto detect"],
    enum_items=[
        {"description": "Not applicable", "value": 0},
        {"description": "External Switch", "value": 1},
        {"description": "External Push Button", "value": 2},
        {"description": "Auto detect", "value": 3},
    ],
)

eep_entities = [eep_ebm]

# YAML without explicit options (should inherit from EEP)
yaml_def_no_options = {
    "component": "select",
    "name": "EBM",
    "channels": [0, 1],
    "config": {
        "entity_category": "config",
        "icon": "mdi:light-switch",
        "command_template": '{"CMD": 11, "IO": {{channel}}, "EBM": {{value}}}',
    },
}

print("\nEEP Entity EBM has enum_options:", eep_ebm.enum_options)
print("EEP Entity EBM has enum_items:", eep_ebm.enum_items)

print("\nExpanding YAML definition with channels [0, 1]...")
expanded = _expand_channel_entities(yaml_def_no_options, eep_entities)

print(f"\nExpanded to {len(expanded)} entities:")
for i, entity_def in enumerate(expanded):
    print(f"\n  Entity {i+1}: {entity_def['name']}")
    print(f"    Description: {entity_def.get('description', 'N/A')}")
    print(f"    Config: {entity_def['config']}")
    
    if 'options' in entity_def['config']:
        print(f"    ✅ Has options in config: {entity_def['config']['options']}")
    else:
        print(f"    ❌ NO options in config!")

print("\n" + "=" * 80)
print("RESULT: enum_options from EEP.xml are", end=" ")
if any('options' in e['config'] for e in expanded):
    print("✅ INHERITED (PASS)")
else:
    print("❌ NOT INHERITED (FAIL)")
print("=" * 80)

print("\n\n" + "=" * 80)
print("TEST 2: Select with explicit YAML options should override EEP.xml")
print("=" * 80)

yaml_def_with_options = {
    "component": "select",
    "name": "EBM",
    "channels": [0, 1],
    "config": {
        "options": ["Custom 1", "Custom 2"],
        "command_template": '{"CMD": 11, "IO": {{channel}}, "EBM": {{value}}}',
    },
}

print("\nYAML has explicit options:", yaml_def_with_options['config']['options'])
print("\nExpanding YAML definition with channels [0, 1]...")
expanded2 = _expand_channel_entities(yaml_def_with_options, eep_entities)

print(f"\nExpanded to {len(expanded2)} entities:")
for i, entity_def in enumerate(expanded2):
    print(f"\n  Entity {i+1}: {entity_def['name']}")
    if 'options' in entity_def['config']:
        print(f"    Options: {entity_def['config']['options']}")
        if entity_def['config']['options'] == ["Custom 1", "Custom 2"]:
            print(f"    ✅ YAML options preserved")
        else:
            print(f"    ❌ YAML options NOT preserved")
    else:
        print(f"    ❌ NO options!")

print("\n" + "=" * 80)
