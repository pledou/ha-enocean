"""Test automatic per-channel entity expansion from mapping YAML.

Tests the 'channels' attribute that allows defining one entity definition
that automatically expands into multiple channel-specific entities.
"""

from __future__ import annotations

import pytest

from custom_components.enocean import eep_devices as ed
from custom_components.enocean.types import EntityType, EEPEntityDef


def test_channels_attribute_expands_to_multiple_entities(monkeypatch: pytest.MonkeyPatch):
    """Test that channels attribute creates separate entities for each channel."""
    # Mock mapping with channels attribute
    mapping = {
        0xD2: {
            0x01: {
                0x12: {
                    "entities": [
                        {
                            "component": "number",
                            "name": "DT1",
                            "description": "Dim timer 1",
                            "channels": [0, 1],
                            "config": {
                                "entity_category": "config",
                                "min_value": 0,
                                "max_value": 127.5,
                                "unit": "s",
                                "command_template": '{"CMD": 2, "IO": {{channel}}, "DT1": {{(value * 2) | int}}}',
                            },
                        }
                    ]
                }
            }
        }
    }

    monkeypatch.setattr(ed, "_load_eep_mapping", lambda: mapping)
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: object())  # Return truthy object
    # Return minimal dummy entity so we don't hit early return
    monkeypatch.setattr(ed, "_extract_eep_fields", lambda _profile, _rorg, _func, _type: [
        EEPEntityDef(description="dummy", rorg=0xD2, rorg_func=0x01, rorg_type=0x12, 
                     data_field="dummy_field", entity_type=EntityType.SENSOR)
    ])

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    # Filter out dummy entity used for testing
    entities = [e for e in entities if e.data_field != "dummy_field"]

    # Should create 2 entities, one for each channel
    assert len(entities) == 2

    # Check entity names
    entity_names = [e.data_field for e in entities]
    assert "DT1_ch0" in entity_names
    assert "DT1_ch1" in entity_names

    # Original "DT1" should not exist
    assert "DT1" not in entity_names


def test_channels_expansion_adds_channel_suffix_to_description(monkeypatch: pytest.MonkeyPatch):
    """Test that channel expansion appends ' - Channel X' to descriptions."""
    mapping = {
        0xD2: {
            0x01: {
                0x12: {
                    "entities": [
                        {
                            "component": "select",
                            "name": "default_state",
                            "description": "Default state after power restore",
                            "channels": [0, 1],
                            "config": {
                                "options": ["OFF", "ON", "Remember"],
                            },
                        }
                    ]
                }
            }
        }
    }

    monkeypatch.setattr(ed, "_load_eep_mapping", lambda: mapping)
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: object())
    monkeypatch.setattr(ed, "_extract_eep_fields", lambda _profile, _rorg, _func, _type: [EEPEntityDef(description="dummy", rorg=0xD2, rorg_func=0x01, rorg_type=0x12, data_field="dummy_field", entity_type=EntityType.SENSOR)])

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    

    # Filter out dummy entity used for testing

    entities = [e for e in entities if e.data_field != "dummy_field"]

    

    entities_by_name = {e.data_field: e for e in entities}

    assert entities_by_name["default_state_ch0"].description == "Default state after power restore - Channel 0"
    assert entities_by_name["default_state_ch1"].description == "Default state after power restore - Channel 1"


def test_channels_expansion_substitutes_channel_in_command_template(
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that {{channel}} placeholder is replaced with actual channel number."""
    mapping = {
        0xD2: {
            0x01: {
                0x12: {
                    "entities": [
                        {
                            "component": "number",
                            "name": "timer",
                            "channels": [0, 1, 2],
                            "config": {
                                "command_template": '{"CMD": 11, "IO": {{channel}}, "AOT": {{value}}}',
                            },
                        }
                    ]
                }
            }
        }
    }

    monkeypatch.setattr(ed, "_load_eep_mapping", lambda: mapping)
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: object())
    monkeypatch.setattr(ed, "_extract_eep_fields", lambda _profile, _rorg, _func, _type: [EEPEntityDef(description="dummy", rorg=0xD2, rorg_func=0x01, rorg_type=0x12, data_field="dummy_field", entity_type=EntityType.SENSOR)])

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    

    # Filter out dummy entity used for testing

    entities = [e for e in entities if e.data_field != "dummy_field"]

    

    entities_by_name = {e.data_field: e for e in entities}

    # Check that {{channel}} was replaced with actual values
    assert '{"CMD": 11, "IO": 0, "AOT": {{value}}}' in entities_by_name["timer_ch0"].command_template
    assert '{"CMD": 11, "IO": 1, "AOT": {{value}}}' in entities_by_name["timer_ch1"].command_template
    assert '{"CMD": 11, "IO": 2, "AOT": {{value}}}' in entities_by_name["timer_ch2"].command_template


def test_channels_expansion_handles_switch_command_templates(
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that switch entities with command_template_on/off get channel substitution."""
    mapping = {
        0xD2: {
            0x01: {
                0x12: {
                    "entities": [
                        {
                            "component": "switch",
                            "name": "local_control",
                            "description": "Enable local control",
                            "channels": [0, 1],
                            "config": {
                                "command_template_on": '{"CMD": 2, "IO": {{channel}}, "LC": 1}',
                                "command_template_off": '{"CMD": 2, "IO": {{channel}}, "LC": 0}',
                            },
                        }
                    ]
                }
            }
        }
    }

    monkeypatch.setattr(ed, "_load_eep_mapping", lambda: mapping)
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: object())
    monkeypatch.setattr(ed, "_extract_eep_fields", lambda _profile, _rorg, _func, _type: [EEPEntityDef(description="dummy", rorg=0xD2, rorg_func=0x01, rorg_type=0x12, data_field="dummy_field", entity_type=EntityType.SENSOR)])

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    

    # Filter out dummy entity used for testing

    entities = [e for e in entities if e.data_field != "dummy_field"]

    

    entities_by_name = {e.data_field: e for e in entities}

    # Check channel 0
    assert '{"CMD": 2, "IO": 0, "LC": 1}' in entities_by_name["local_control_ch0"].command_template_on
    assert '{"CMD": 2, "IO": 0, "LC": 0}' in entities_by_name["local_control_ch0"].command_template_off

    # Check channel 1
    assert '{"CMD": 2, "IO": 1, "LC": 1}' in entities_by_name["local_control_ch1"].command_template_on
    assert '{"CMD": 2, "IO": 1, "LC": 0}' in entities_by_name["local_control_ch1"].command_template_off


def test_channels_expansion_preserves_entity_type(monkeypatch: pytest.MonkeyPatch):
    """Test that expanded entities maintain correct entity type."""
    mapping = {
        0xD2: {
            0x01: {
                0x12: {
                    "entities": [
                        {
                            "component": "number",
                            "name": "num_field",
                            "channels": [0, 1],
                            "config": {},
                        },
                        {
                            "component": "select",
                            "name": "select_field",
                            "channels": [0, 1],
                            "config": {"options": ["A", "B"]},
                        },
                        {
                            "component": "switch",
                            "name": "switch_field",
                            "channels": [0, 1],
                            "config": {},
                        },
                    ]
                }
            }
        }
    }

    monkeypatch.setattr(ed, "_load_eep_mapping", lambda: mapping)
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: object())
    monkeypatch.setattr(ed, "_extract_eep_fields", lambda _profile, _rorg, _func, _type: [EEPEntityDef(description="dummy", rorg=0xD2, rorg_func=0x01, rorg_type=0x12, data_field="dummy_field", entity_type=EntityType.SENSOR)])

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    

    # Filter out dummy entity used for testing

    entities = [e for e in entities if e.data_field != "dummy_field"]

    

    entities_by_name = {e.data_field: e for e in entities}

    # Check entity types are preserved
    assert entities_by_name["num_field_ch0"].entity_type == EntityType.NUMBER
    assert entities_by_name["num_field_ch1"].entity_type == EntityType.NUMBER
    assert entities_by_name["select_field_ch0"].entity_type == EntityType.SELECT
    assert entities_by_name["select_field_ch1"].entity_type == EntityType.SELECT
    assert entities_by_name["switch_field_ch0"].entity_type == EntityType.SWITCH
    assert entities_by_name["switch_field_ch1"].entity_type == EntityType.SWITCH


def test_channels_expansion_preserves_all_config_attributes(
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that all config attributes are copied to expanded entities."""
    mapping = {
        0xD2: {
            0x01: {
                0x12: {
                    "entities": [
                        {
                            "component": "number",
                            "name": "full_config",
                            "channels": [0],
                            "config": {
                                "entity_category": "config",
                                "min_value": 0,
                                "max_value": 100,
                                "step": 0.5,
                                "unit": "s",
                                "icon": "mdi:timer",
                                "device_class": "duration",
                                "mode": "box",
                            },
                        }
                    ]
                }
            }
        }
    }

    monkeypatch.setattr(ed, "_load_eep_mapping", lambda: mapping)
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: object())
    monkeypatch.setattr(ed, "_extract_eep_fields", lambda _profile, _rorg, _func, _type: [EEPEntityDef(description="dummy", rorg=0xD2, rorg_func=0x01, rorg_type=0x12, data_field="dummy_field", entity_type=EntityType.SENSOR)])

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    # Filter out dummy entity used for testing
    entities = [e for e in entities if e.data_field != "dummy_field"]
    
    entity = entities[0]

    # Verify all attributes are present
    assert entity.unit == "s"
    assert entity.icon == "mdi:timer"
    assert entity.min_value == 0
    assert entity.max_value == 100
    assert entity.device_class == "duration"
    assert entity.mode == "box"


def test_no_channels_attribute_works_as_before(monkeypatch: pytest.MonkeyPatch):
    """Test that entities without 'channels' attribute work normally."""
    mapping = {
        0xD2: {
            0x01: {
                0x12: {
                    "entities": [
                        {
                            "component": "button",
                            "name": "query_status",
                            "config": {
                                "command_template": '{"CMD": 3, "IO": 30}',
                            },
                        }
                    ]
                }
            }
        }
    }

    monkeypatch.setattr(ed, "_load_eep_mapping", lambda: mapping)
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: object())
    monkeypatch.setattr(ed, "_extract_eep_fields", lambda _profile, _rorg, _func, _type: [EEPEntityDef(description="dummy", rorg=0xD2, rorg_func=0x01, rorg_type=0x12, data_field="dummy_field", entity_type=EntityType.SENSOR)])

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    

    # Filter out dummy entity used for testing

    entities = [e for e in entities if e.data_field != "dummy_field"]

    

    # Should create exactly 1 entity with original name
    assert len(entities) == 1
    assert entities[0].data_field == "query_status"
    assert entities[0].entity_type == EntityType.BUTTON


def test_channels_empty_list_creates_no_entities(monkeypatch: pytest.MonkeyPatch):
    """Test that channels=[] creates no entities."""
    mapping = {
        0xD2: {
            0x01: {
                0x12: {
                    "entities": [
                        {
                            "component": "number",
                            "name": "disabled",
                            "channels": [],
                            "config": {},
                        }
                    ]
                }
            }
        }
    }

    monkeypatch.setattr(ed, "_load_eep_mapping", lambda: mapping)
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: object())
    monkeypatch.setattr(ed, "_extract_eep_fields", lambda _profile, _rorg, _func, _type: [EEPEntityDef(description="dummy", rorg=0xD2, rorg_func=0x01, rorg_type=0x12, data_field="dummy_field", entity_type=EntityType.SENSOR)])

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    

    # Filter out dummy entity used for testing

    entities = [e for e in entities if e.data_field != "dummy_field"]

    

    # Should create no entities
    assert len(entities) == 0


def test_channels_with_multiple_template_substitutions(monkeypatch: pytest.MonkeyPatch):
    """Test that {{channel}} is replaced in all occurrences within a template."""
    mapping = {
        0xD2: {
            0x01: {
                0x12: {
                    "entities": [
                        {
                            "component": "number",
                            "name": "multi_ref",
                            "channels": [5],
                            "config": {
                                "command_template": '{"CMD": {{channel}}, "IO": {{channel}}, "VAL": {{value}}}',
                            },
                        }
                    ]
                }
            }
        }
    }

    monkeypatch.setattr(ed, "_load_eep_mapping", lambda: mapping)
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: object())
    monkeypatch.setattr(ed, "_extract_eep_fields", lambda _profile, _rorg, _func, _type: [EEPEntityDef(description="dummy", rorg=0xD2, rorg_func=0x01, rorg_type=0x12, data_field="dummy_field", entity_type=EntityType.SENSOR)])

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    # Filter out dummy entity used for testing
    entities = [e for e in entities if e.data_field != "dummy_field"]

    # Both occurrences of {{channel}} should be replaced
    assert '{"CMD": 5, "IO": 5, "VAL": {{value}}}' in entities[0].command_template


def test_channels_stores_channel_number_in_offset(monkeypatch: pytest.MonkeyPatch):
    """Test that the channel number is stored in entity.offset for state matching."""
    mapping = {
        0xD2: {
            0x01: {
                0x12: {
                    "entities": [
                        {
                            "component": "light",
                            "name": "channel",
                            "channels": [0, 1],
                            "config": {
                                "command_template_on": '{"CMD": 1, "IO": {{channel}}, "OV": 100}',
                                "command_template_off": '{"CMD": 1, "IO": {{channel}}, "OV": 0}',
                            },
                        }
                    ]
                }
            }
        }
    }

    monkeypatch.setattr(ed, "_load_eep_mapping", lambda: mapping)
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: object())
    monkeypatch.setattr(ed, "_extract_eep_fields", lambda _profile, _rorg, _func, _type: [EEPEntityDef(description="dummy", rorg=0xD2, rorg_func=0x01, rorg_type=0x12, data_field="dummy_field", entity_type=EntityType.SENSOR)])

    entities = ed.get_entities_for_device(
        {"rorg": 0xD2, "rorg_func": 0x01, "rorg_type": 0x12, "manufacturer": None}
    )

    

    # Filter out dummy entity used for testing

    entities = [e for e in entities if e.data_field != "dummy_field"]

    

    entities_by_name = {e.data_field: e for e in entities}

    # Check that offset stores the channel number for state update matching
    assert entities_by_name["channel_ch0"].offset == 0
    assert entities_by_name["channel_ch1"].offset == 1
