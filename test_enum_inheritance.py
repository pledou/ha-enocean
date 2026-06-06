#!/usr/bin/env python3
"""
Manual test for enum_options/enum_items inheritance in channel expansion.
This test directly imports and tests the _expand_channel_entities function.
"""

import sys
import os

# Add path to custom component
sys.path.insert(0, '/config/ha-enocean-repo')
sys.path.insert(0, '/config/enocean')

# Import from the custom component's types module
from custom_components.enocean.types import EEPEntityDef, EntityType

print("="*80)
print("Testing enum_options and enum_items inheritance in channel expansion")
print("="*80)

# Create a mock EEP entity for EBM with enum options
print("\n1. Creating mock EEP entity for EBM field...")
eep_ebm = EEPEntityDef(
    description="External Button Mode",
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

print(f"   EEP Entity: {eep_ebm.data_field}")
print(f"   enum_options: {eep_ebm.enum_options}")
print(f"   enum_items: {eep_ebm.enum_items}")

eep_entities = [eep_ebm]

# Now import the function to test
from custom_components.enocean.eep_devices import _expand_channel_entities

# TEST 1: YAML without explicit options (should inherit from EEP)
print("\n" + "="*80)
print("TEST 1: YAML without options → should inherit from EEP.xml")
print("="*80)

yaml_def_no_options = {
    "component": "select",
    "name": "EBM",
    "channels": [0, 1],
    "config": {
        "entity_category": "config",
        "command_template": '{"CMD": 11, "IO": {{channel}}, "EBM": {{value}}}',
    },
}

print("\nYAML definition:")
print(f"   name: {yaml_def_no_options['name']}")
print(f"   channels: {yaml_def_no_options['channels']}")
print(f"   config.options: {yaml_def_no_options['config'].get('options', 'NOT SPECIFIED')}")

print("\nExpanding...")
expanded = _expand_channel_entities(yaml_def_no_options, eep_entities)

print(f"\nResult: Expanded to {len(expanded)} entities")
test1_passed = True
for i, entity_def in enumerate(expanded):
    print(f"\n  Entity {i+1}: {entity_def['name']}")
    print(f"    description: {entity_def.get('description', 'N/A')}")
    
    config = entity_def.get('config', {})
    options = config.get('options')
    enum_items = config.get('_enum_items')
    
    print(f"    config.options: {options}")
    print(f"    config._enum_items: {enum_items}")
    
    if options == eep_ebm.enum_options:
        print(f"    ✅ options correctly inherited from EEP")
    else:
        print(f"    ❌ options NOT inherited (expected {eep_ebm.enum_options})")
        test1_passed = False
    
    if enum_items == eep_ebm.enum_items:
        print(f"    ✅ enum_items correctly inherited from EEP")
    else:
        print(f"    ❌ enum_items NOT inherited (expected {eep_ebm.enum_items})")
        test1_passed = False

if test1_passed:
    print("\n✅ TEST 1 PASSED: Options and enum_items inherited correctly")
else:
    print("\n❌ TEST 1 FAILED: Options or enum_items not inherited")

# TEST 2: YAML with explicit options (should override EEP, no enum_items)
print("\n" + "="*80)
print("TEST 2: YAML with options → should use YAML options, NOT inherit enum_items")
print("="*80)

yaml_def_with_options = {
    "component": "select",
    "name": "EBM",
    "channels": [0, 1],
    "config": {
        "options": ["Custom 1", "Custom 2"],
        "command_template": '{"CMD": 11, "IO": {{channel}}, "EBM": {{value}}}',
    },
}

print("\nYAML definition:")
print(f"   name: {yaml_def_with_options['name']}")
print(f"   channels: {yaml_def_with_options['channels']}")
print(f"   config.options: {yaml_def_with_options['config']['options']}")

print("\nExpanding...")
expanded2 = _expand_channel_entities(yaml_def_with_options, eep_entities)

print(f"\nResult: Expanded to {len(expanded2)} entities")
test2_passed = True
for i, entity_def in enumerate(expanded2):
    print(f"\n  Entity {i+1}: {entity_def['name']}")
    
    config = entity_def.get('config', {})
    options = config.get('options')
    enum_items = config.get('_enum_items')
    
    print(f"    config.options: {options}")
    print(f"    config._enum_items: {enum_items}")
    
    if options == ["Custom 1", "Custom 2"]:
        print(f"    ✅ YAML options preserved")
    else:
        print(f"    ❌ YAML options NOT preserved (got {options})")
        test2_passed = False
    
    if enum_items is None:
        print(f"    ✅ enum_items correctly NOT inherited (since YAML overrides options)")
    else:
        print(f"    ❌ enum_items incorrectly inherited (should be None)")
        test2_passed = False

if test2_passed:
    print("\n✅ TEST 2 PASSED: YAML options preserved, enum_items not inherited")
else:
    print("\n❌ TEST 2 FAILED: YAML options not preserved or enum_items inherited incorrectly")

# TEST 3: Entity without channels attribute (should not expand)
print("\n" + "="*80)
print("TEST 3: YAML without channels → should not expand")
print("="*80)

yaml_def_no_channels = {
    "component": "select",
    "name": "DS",
    "config": {
        "options": ["Option 1", "Option 2"],
    },
}

print("\nYAML definition:")
print(f"   name: {yaml_def_no_channels['name']}")
print(f"   channels: {yaml_def_no_channels.get('channels', 'NOT SPECIFIED')}")

print("\nExpanding...")
expanded3 = _expand_channel_entities(yaml_def_no_channels, eep_entities)

print(f"\nResult: Expanded to {len(expanded3)} entities")
test3_passed = (len(expanded3) == 1 and expanded3[0]['name'] == 'DS')

if test3_passed:
    print(f"  Entity: {expanded3[0]['name']}")
    print(f"  ✅ Entity not expanded (as expected)")
else:
    print(f"  ❌ Entity incorrectly expanded or modified")

if test3_passed:
    print("\n✅ TEST 3 PASSED: Non-channel entities not expanded")
else:
    print("\n❌ TEST 3 FAILED: Non-channel entity was modified")

# Summary
print("\n" + "="*80)
print("SUMMARY")
print("="*80)
all_passed = test1_passed and test2_passed and test3_passed
if all_passed:
    print("✅ ALL TESTS PASSED")
    sys.exit(0)
else:
    print("❌ SOME TESTS FAILED")
    sys.exit(1)
