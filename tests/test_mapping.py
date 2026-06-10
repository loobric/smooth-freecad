# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the v2 facade mapping (smooth-freecad#5).

The contract under test:
- .fctb -> ToolRecord -> .fctb is LOSSLESS apart from the additive
  'smooth' identity key (unknown keys like FreeCAD's F&S 'presets'
  survive verbatim)
- server-side canonical edits (name, diameter) overlay on regeneration;
  unchanged values keep their original formatting
- .fctl round-trips with per-tool numbers preserved
"""
import copy
import json
from pathlib import Path

import pytest

from freecad.Smooth.mapping import (
    fctb_to_record, record_to_fctb, fctl_to_library, library_to_fctl,
    parse_quantity, format_quantity,
)

BITS = Path(__file__).parent / "fixtures" / "bits"
LIBS = Path(__file__).parent / "fixtures" / "libraries"
ALL_BITS = sorted(BITS.glob("*.fctb"))


def fake_record(payload, record_id="rec-1", version=1):
    """What the server would return for an exported payload."""
    return {**payload, "id": record_id, "version": version}


@pytest.mark.unit
def test_parse_and_format_quantity():
    assert parse_quantity("6.00 mm") == (6.0, "mm")
    assert parse_quantity("3.175 mm") == (3.175, "mm")
    assert parse_quantity("60.00 °") == (60.0, "°")
    assert parse_quantity("Forward") == (None, None)
    assert format_quantity(6.0, "mm") == "6.00 mm"
    assert format_quantity(3.175, "mm") == "3.175 mm"


@pytest.mark.unit
@pytest.mark.parametrize("path", ALL_BITS, ids=lambda p: p.stem)
def test_every_fixture_bit_round_trips_lossless(path):
    """fctb -> record -> fctb returns the identical document plus the
    additive smooth key. All seven shape types in the fixtures."""
    original = json.loads(path.read_text())
    payload, prior_id = fctb_to_record(original)
    assert prior_id is None  # fixtures were never exported

    regenerated = record_to_fctb(fake_record(payload))
    smooth = regenerated.pop("smooth")
    assert smooth == {"record_id": "rec-1", "version": 1}
    assert regenerated == original


@pytest.mark.unit
def test_unknown_keys_survive_including_presets():
    """The additive F&S presets key (FreeCAD PR #30078) — and any other
    unknown key — must survive the round trip verbatim. This is the M3
    preset-sync carrier."""
    doc = json.loads((BITS / "end_mill_6.0mm_2f.fctb").read_text())
    doc["presets"] = [{"name": "alu-6061", "surface_speed": 400, "chipload": 0.05}]
    doc["somebody-elses-key"] = {"nested": [1, 2, 3]}

    payload, _ = fctb_to_record(doc)
    regenerated = record_to_fctb(fake_record(payload))
    assert regenerated["presets"] == doc["presets"]
    assert regenerated["somebody-elses-key"] == doc["somebody-elses-key"]


@pytest.mark.unit
def test_canonical_geometry_extracted():
    doc = json.loads((BITS / "end_mill_6.0mm_2f.fctb").read_text())
    payload, _ = fctb_to_record(doc)
    g = payload["geometry"]
    assert g["shape"] == "endmill"
    assert g["diameter"] == 6.0 and g["diameter_unit"] == "mm"
    assert g["flutes"] == 2
    assert payload["name"] == "End Mill 6.0mm 2F"


@pytest.mark.unit
def test_smooth_identity_key_round_trips():
    """A re-exported bit carries its server id; the stored extra copy is
    clean of the plumbing key."""
    doc = json.loads((BITS / "drill_5.0mm.fctb").read_text())
    doc["smooth"] = {"record_id": "rec-99", "version": 3}
    payload, prior_id = fctb_to_record(doc)
    assert prior_id == "rec-99"
    assert "smooth" not in payload["extra"]["freecad"]["fctb"]


@pytest.mark.unit
def test_server_side_edits_overlay_on_regeneration():
    """Name and diameter changed on the server appear in the file; an
    unchanged parameter keeps its original string verbatim."""
    doc = json.loads((BITS / "end_mill_6.0mm_2f.fctb").read_text())
    payload, _ = fctb_to_record(doc)
    record = fake_record(payload, version=2)
    record["name"] = "renamed on server"
    record["geometry"] = dict(record["geometry"], diameter=6.35)

    regenerated = record_to_fctb(record)
    assert regenerated["name"] == "renamed on server"
    assert regenerated["parameter"]["Diameter"] == "6.35 mm"
    # untouched values keep original formatting
    assert regenerated["parameter"]["Length"] == doc["parameter"]["Length"]


@pytest.mark.unit
def test_record_without_freecad_origin_gets_minimal_doc():
    """A record created elsewhere (curl, importer) still exports a valid
    .fctb built from canonical geometry."""
    record = {"id": "rec-7", "version": 1, "name": '1/4" downcut',
              "geometry": {"shape": "endmill", "diameter": 6.35,
                           "diameter_unit": "mm", "flutes": 2}}
    doc = record_to_fctb(record)
    assert doc["name"] == '1/4" downcut'
    assert doc["shape-type"] == "Endmill"
    assert doc["parameter"]["Diameter"] == "6.35 mm"
    assert doc["parameter"]["Flutes"] == 2
    assert doc["smooth"]["record_id"] == "rec-7"


@pytest.mark.unit
def test_fctl_round_trip_preserves_numbers_and_order():
    fctl = json.loads(next(LIBS.glob("*.fctl")).read_text())
    paths = [t["path"] for t in fctl["tools"]]
    id_by_path = {p: f"rec-{i}" for i, p in enumerate(paths)}

    payload, unresolved, prior_id = fctl_to_library(fctl, id_by_path)
    assert unresolved == [] and prior_id is None
    assert payload["name"] == fctl["label"]
    assert len(payload["tool_record_ids"]) == len(fctl["tools"])

    library = fake_record(payload, record_id="lib-1")
    path_by_id = {v: k for k, v in id_by_path.items()}
    regenerated, unresolved = library_to_fctl(library, path_by_id)
    assert unresolved == []
    assert regenerated.pop("smooth") == {"library_id": "lib-1", "version": 1}
    assert regenerated == fctl


@pytest.mark.unit
def test_fctl_unresolved_paths_are_reported_not_dropped():
    fctl = {"label": "x", "version": 1,
            "tools": [{"nr": 1, "path": "known.fctb"}, {"nr": 2, "path": "mystery.fctb"}]}
    payload, unresolved, _ = fctl_to_library(fctl, {"known.fctb": "rec-1"})
    assert unresolved == ["mystery.fctb"]
    assert payload["tool_record_ids"] == ["rec-1"]


@pytest.mark.unit
def test_member_added_server_side_gets_next_free_number():
    library = {"id": "lib-1", "version": 2, "name": "default",
               "tool_record_ids": ["rec-1", "rec-new"],
               "extra": {"freecad": {"label": "default", "version": 1,
                                     "numbers": {"rec-1": 11}}}}
    doc, unresolved = library_to_fctl(
        library, {"rec-1": "a.fctb", "rec-new": "b.fctb"})
    assert unresolved == []
    assert doc["tools"] == [{"nr": 11, "path": "a.fctb"},
                            {"nr": 12, "path": "b.fctb"}]
