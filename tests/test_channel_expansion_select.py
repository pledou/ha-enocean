"""Test channel expansion for select entities with enum options from EEP.xml."""

import pytest
from custom_components.enocean import eep_devices as ed
from custom_components.enocean.types import EEPEntityDef, EntityType


def test_channels_expansion_inherits_enum_options_from_eep(monkeypatch: pytest.MonkeyPatch):
    """Test that select entities with channels inherit enum options from EEP.xml."""
    # Create a mock EEP entity for EBM (External Button Mode) with enum options
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

    mapping = {
        0xD2: {
            0x01: {
                0x12: {
                    "entities": [yaml_def_no_options]
                }
            }
        }
    }

    monkeypatch.setattr(ed, "_load_eep_mapping", lambda: mapping)
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: object())
    monkeypatch.setattr(
        ed,
        "_extract_eep_fields",
        lambda _profile, _rorg, _func, _type: eep_entities,
    )

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    # Filter to get only EBM channel entities
    ebm_entities = [e for e in entities if e.data_field.startswith("EBM_ch")]

    assert len(ebm_entities) == 2, f"Expected 2 EBM entities, got {len(ebm_entities)}"

    ebm_ch0 = next((e for e in ebm_entities if e.data_field == "EBM_ch0"), None)
    ebm_ch1 = next((e for e in ebm_entities if e.data_field == "EBM_ch1"), None)

    assert ebm_ch0 is not None, "EBM_ch0 entity not found"
    assert ebm_ch1 is not None, "EBM_ch1 entity not found"

    # Verify options were inherited from EEP.xml
    assert ebm_ch0.enum_options is not None, "EBM_ch0 should have enum_options"
    assert ebm_ch0.enum_options == [
        "Not applicable",
        "External Switch",
        "External Push Button",
        "Auto detect",
    ], f"EBM_ch0 options: {ebm_ch0.enum_options}"

    assert ebm_ch1.enum_options is not None, "EBM_ch1 should have enum_options"
    assert ebm_ch1.enum_options == [
        "Not applicable",
        "External Switch",
        "External Push Button",
        "Auto detect",
    ], f"EBM_ch1 options: {ebm_ch1.enum_options}"

    # Verify enum_items were inherited for value lookup
    assert ebm_ch0.enum_items is not None, "EBM_ch0 should have enum_items"
    assert len(ebm_ch0.enum_items) == 4, f"EBM_ch0 enum_items count: {len(ebm_ch0.enum_items)}"

    assert ebm_ch1.enum_items is not None, "EBM_ch1 should have enum_items"
    assert len(ebm_ch1.enum_items) == 4, f"EBM_ch1 enum_items count: {len(ebm_ch1.enum_items)}"


def test_channels_expansion_yaml_options_override_eep(monkeypatch: pytest.MonkeyPatch):
    """Test that explicit YAML options override EEP.xml options."""
    # Create a mock EEP entity with options
    eep_entity = EEPEntityDef(
        description="Test field",
        rorg=0xD2,
        rorg_func=0x01,
        rorg_type=0x12,
        data_field="TEST",
        entity_type=EntityType.SELECT,
        enum_options=["Option A", "Option B"],
        enum_items=[
            {"description": "Option A", "value": 0},
            {"description": "Option B", "value": 1},
        ],
    )

    eep_entities = [eep_entity]

    # YAML with explicit options (should override EEP)
    yaml_def_with_options = {
        "component": "select",
        "name": "TEST",
        "channels": [0, 1],
        "config": {
            "options": ["Custom 1", "Custom 2", "Custom 3"],
            "command_template": '{"CMD": 1, "IO": {{channel}}, "TEST": {{value}}}',
        },
    }

    mapping = {
        0xD2: {
            0x01: {
                0x12: {
                    "entities": [yaml_def_with_options]
                }
            }
        }
    }

    monkeypatch.setattr(ed, "_load_eep_mapping", lambda: mapping)
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: object())
    monkeypatch.setattr(
        ed,
        "_extract_eep_fields",
        lambda _profile, _rorg, _func, _type: eep_entities,
    )

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    # Filter to get only TEST channel entities
    test_entities = [e for e in entities if e.data_field.startswith("TEST_ch")]

    assert len(test_entities) == 2

    test_ch0 = test_entities[0]

    # Verify YAML options override EEP options
    assert test_ch0.enum_options == [
        "Custom 1",
        "Custom 2",
        "Custom 3",
    ], f"YAML options should override EEP: {test_ch0.enum_options}"


def test_per_channel_entities_not_auto_generated(monkeypatch: pytest.MonkeyPatch):
    """Test that entities handled by channel expansion are not auto-generated."""
    # Create mock EEP entities
    eep_entities = [
        EEPEntityDef(
            description="Dim timer 1",
            rorg=0xD2,
            rorg_func=0x01,
            rorg_type=0x12,
            data_field="DT1",
            entity_type=EntityType.NUMBER,
            min_value=0,
            max_value=255,
        ),
        EEPEntityDef(
            description="External Switch/Push Button",
            rorg=0xD2,
            rorg_func=0x01,
            rorg_type=0x12,
            data_field="EBM",
            entity_type=EntityType.SELECT,
            enum_options=["Not applicable", "External Switch"],
        ),
    ]

    # YAML configuration that uses channels AND skips auto-generation
    mapping = {
        0xD2: {
            0x01: {
                0x12: {
                    "entities": [
                        {
                            "component": "number",
                            "name": "DT1",
                            "channels": [0, 1],
                            "config": {
                                "command_template": '{"CMD": 2, "IO": {{channel}}, "DT1": {{value}}}',
                            },
                        },
                        {
                            "component": "select",
                            "name": "EBM",
                            "channels": [0, 1],
                            "config": {
                                "command_template": '{"CMD": 11, "IO": {{channel}}, "EBM": {{value}}}',
                            },
                        },
                        # Explicitly skip auto-generation
                        {"name": "DT1", "component": None},
                        {"name": "EBM", "component": None},
                    ]
                }
            }
        }
    }

    monkeypatch.setattr(ed, "_load_eep_mapping", lambda: mapping)
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: object())
    monkeypatch.setattr(
        ed,
        "_extract_eep_fields",
        lambda _profile, _rorg, _func, _type: eep_entities,
    )

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    # Count entities by data_field
    dt1_entities = [e for e in entities if "DT1" in e.data_field]
    ebm_entities = [e for e in entities if "EBM" in e.data_field]

    # Should only have the channel-expanded entities (DT1_ch0, DT1_ch1, EBM_ch0, EBM_ch1)
    # NOT the base entities (DT1, EBM)
    assert len(dt1_entities) == 2, f"Expected 2 DT1 entities (ch0, ch1), got {len(dt1_entities)}: {[e.data_field for e in dt1_entities]}"
    assert len(ebm_entities) == 2, f"Expected 2 EBM entities (ch0, ch1), got {len(ebm_entities)}: {[e.data_field for e in ebm_entities]}"

    # Verify we have the channel entities
    assert any(e.data_field == "DT1_ch0" for e in dt1_entities)
    assert any(e.data_field == "DT1_ch1" for e in dt1_entities)
    assert any(e.data_field == "EBM_ch0" for e in ebm_entities)
    assert any(e.data_field == "EBM_ch1" for e in ebm_entities)

    # Verify we DON'T have the base entities
    assert not any(e.data_field == "DT1" for e in entities), "Base DT1 entity should not exist"
    assert not any(e.data_field == "EBM" for e in entities), "Base EBM entity should not exist"
