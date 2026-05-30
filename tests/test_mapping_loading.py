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


def test_f6_missing_profile_uses_generic_stateless_buttons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown F6 profile should still expose a generic set of stateless buttons."""
    monkeypatch.setattr(ed, "_load_eep_profile", lambda _profile: None)

    entities = ed.get_entities_for_device(
        {
            "rorg": 0xF6,
            "rorg_func": 0x55,
            "rorg_type": 0x99,
            "manufacturer": None,
        }
    )

    names = {ent.data_field for ent in entities}
    assert {"R1_AI", "R1_AO", "R1_BI", "R1_BO"}.issubset(names)
    assert all(ent.entity_type.value == "button" for ent in entities)


def test_f6_unmapped_profile_logs_warning_and_uses_generic_buttons(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Learned-but-unmapped F6 profile should be explicit and still pair with generic buttons."""
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

    assert entities
    assert "using generic stateless button mapping" in caplog.text.lower()
