"""Test that component: null prevents entity creation and channels inherit options."""
import pytest
from custom_components.enocean import eep_devices as ed
from custom_components.enocean.types import EEPEntityDef, EntityType


def test_component_null_skips_entity_creation(monkeypatch: pytest.MonkeyPatch):
    """Test that entities with component: null in mapping are not created."""
    # Create mock EEP entities from EEP.xml
    eep_dt1 = EEPEntityDef(
        description="Dim timer 1",
        rorg=0xD2,
        rorg_func=0x01,
        rorg_type=0x12,
        data_field="DT1",
        entity_type=EntityType.SELECT,
        min_value=0,
        max_value=255,
    )
    eep_ds = EEPEntityDef(
        description="Default state",
        rorg=0xD2,
        rorg_func=0x01,
        rorg_type=0x12,
        data_field="DS",
        entity_type=EntityType.SELECT,
        enum_options=["OFF", "ON", "Remember"],
    )
    
    # Mapping with component: null to skip these entities
    mapping = {
        0xD2: {
            0x01: {
                0x12: {
                    "entities": [
                        {
                            "name": "DT1",
                            "component": None,  # Skip this entity
                        },
                        {
                            "name": "DS",
                            "component": None,  # Skip this entity
                        },
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
        lambda _profile, _rorg, _func, _type: [eep_dt1, eep_ds],
    )

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    # Entities should be filtered out
    assert len(entities) == 0, f"Expected 0 entities but got {len(entities)}: {[e.data_field for e in entities]}"


def test_channels_inherit_options_from_eep(monkeypatch: pytest.MonkeyPatch):
    """Test that channel-expanded select entities inherit options from EEP.xml."""
    # Create mock EEP entity with enum options
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
    
    # Mapping with channels but NO options specified
    mapping = {
        0xD2: {
            0x01: {
                0x12: {
                    "entities": [
                        {
                            "component": "select",
                            "name": "EBM",
                            "channels": [0, 1],
                            "config": {
                                "entity_category": "config",
                                "icon": "mdi:light-switch",
                                "command_template": '{"CMD": 11, "IO": {{channel}}, "EBM": {{value}}}',
                            },
                        }
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
        lambda _profile, _rorg, _func, _type: [eep_ebm],
    )

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    # Should have 2 entities: EBM_ch0 and EBM_ch1
    assert len(entities) == 2, f"Expected 2 entities but got {len(entities)}"
    
    ebm_ch0 = next((e for e in entities if e.data_field == "EBM_ch0"), None)
    ebm_ch1 = next((e for e in entities if e.data_field == "EBM_ch1"), None)
    
    assert ebm_ch0 is not None, "EBM_ch0 not found"
    assert ebm_ch1 is not None, "EBM_ch1 not found"
    
    # Both should have inherited options from EEP.xml
    assert ebm_ch0.enum_options == ["Not applicable", "External Switch", "External Push Button", "Auto detect"]
    assert ebm_ch1.enum_options == ["Not applicable", "External Switch", "External Push Button", "Auto detect"]
    
    # And enum_items for value lookup
    assert ebm_ch0.enum_items is not None
    assert len(ebm_ch0.enum_items) == 4
    assert ebm_ch1.enum_items is not None
    assert len(ebm_ch1.enum_items) == 4


def test_channel_expansion_auto_skips_base_entity(monkeypatch: pytest.MonkeyPatch):
    """Test that using channels attribute automatically skips the base EEP.xml entity."""
    # Create mock EEP entity
    eep_dt1 = EEPEntityDef(
        description="Dim timer 1",
        rorg=0xD2,
        rorg_func=0x01,
        rorg_type=0x12,
        data_field="DT1",
        entity_type=EntityType.NUMBER,
        min_value=0,
        max_value=255,
    )
    
    # Mapping with channels - NO component: null needed!
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
                                "entity_category": "config",
                                "command_template": '{"CMD": 11, "IO": {{channel}}, "DT1": {{value}}}',
                            },
                        }
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
        lambda _profile, _rorg, _func, _type: [eep_dt1],
    )

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    # Should have 2 entities: DT1_ch0 and DT1_ch1 (base DT1 auto-skipped)
    assert len(entities) == 2, f"Expected 2 entities but got {len(entities)}: {[e.data_field for e in entities]}"
    
    # Verify only channel entities exist
    entity_names = [e.data_field for e in entities]
    assert "DT1_ch0" in entity_names
    assert "DT1_ch1" in entity_names
    assert "DT1" not in entity_names, "Base DT1 should have been auto-skipped"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
