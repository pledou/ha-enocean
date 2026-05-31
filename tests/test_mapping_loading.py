"""Pytest tests for EEP mapping loading and conversion.

These tests assert the presence of the VentilAirSec mapping (RORG 0xD2 FUNC 0x01 TYPE 0x00)
and verify that `_build_entities_from_mapping` converts YAML entries into
EEPEntityDef-like objects with expected attributes.
"""

from __future__ import annotations

import logging

import pytest

from custom_components.enocean import eep_devices as ed
from custom_components.enocean.eep_devices import _load_eep_mapping


def test_mapping_contains_d2_func01_type00() -> None:
    """Mapping file contains RORG 0xD2 / FUNC 0x01 / TYPE 0x00 with entities."""
    mapping = _load_eep_mapping()
    assert isinstance(mapping, dict)

    # Ensure RORG 0xD2 exists
    assert 0xD2 in mapping, "Expected RORG 0xD2 in mapping"
    func_entry = mapping[0xD2].get(0x01)
    assert func_entry is not None, "Expected FUNC 0x01 under RORG 0xD2"

    type_entry = func_entry.get(0x00)
    assert type_entry is not None, "Expected TYPE 0x00 under FUNC 0x01"

    entities = type_entry.get("entities", [])
    assert isinstance(entities, list)

    # There should be at least one number entity defined for this profile
    assert any(e.get("component") == "number" for e in entities), (
        "Expected at least one 'number' component in entities for D2/01/00"
    )


def test_f6_missing_profile_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown F6 profile without EEP definition should return empty list."""
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: None)

    entities = ed.get_entities_for_device(
        {
            "rorg": 0xF6,
            "rorg_func": 0x55,
            "rorg_type": 0x99,
            "manufacturer": None,
        }
    )

    # Without EEP profile, we cannot create valid entities
    assert entities == []


def test_f6_unmapped_profile_logs_warning_and_returns_empty(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """F6 profile without fields should log warning and return empty list."""
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: object())
    monkeypatch.setattr(
        ed,
        "_extract_eep_fields",
        lambda _profile, _rorg, _func, _type: [],
    )
    monkeypatch.setattr(ed, "_load_eep_mapping", lambda: {})

    with caplog.at_level(logging.WARNING):
        entities = ed.get_entities_for_device(
            {
                "rorg": 0xF6,
                "rorg_func": 0x12,
                "rorg_type": 0x34,
                "manufacturer": None,
            }
        )

    assert entities == []
    assert "profile has no fields" in caplog.text.lower()


def test_f6_profile_converts_enum_fields_to_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F6 profiles should convert enum fields to event entities with proper event types."""
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: object())
    monkeypatch.setattr(
        ed,
        "_extract_eep_fields",
        lambda _profile, rorg, func, type_: [
            ed.EEPEntityDef(
                description="Rocker 1st action",
                rorg=rorg,
                rorg_func=func,
                rorg_type=type_,
                data_field="R1",
                entity_type=ed.EntityType.SENSOR,  # Will be converted to EVENT
                enum_items=[
                    {"value": 0, "description": "Button AI"},
                    {"value": 1, "description": "Button AO"},
                    {"value": 2, "description": "Button BI"},
                    {"value": 3, "description": "Button BO"},
                ],
                offset=0,
            )
        ],
    )
    monkeypatch.setattr(ed, "_load_eep_mapping", lambda: {})

    entities = ed.get_entities_for_device(
        {
            "rorg": 0xF6,
            "rorg_func": 0x02,
            "rorg_type": 0x01,
            "manufacturer": None,
        }
    )

    assert len(entities) == 1
    event_entity = entities[0]
    assert event_entity.data_field == "R1"
    assert event_entity.entity_type == ed.EntityType.EVENT
    assert event_entity.enum_options == [
        "Button AI",
        "Button AO",
        "Button BI",
        "Button BO",
    ]
