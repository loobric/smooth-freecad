# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the sectioned-schema mapping (docs/TOOL_SCHEMA.md).

The contract under test:
- .fctb -> ToolInstanceRecord sections -> .fctb is LOSSLESS apart from the
  additive 'smooth' identity key: the full document rides verbatim in
  clients.freecad.data.fctb, so unknown keys (FreeCAD's F&S 'presets') survive.
- the FreeCAD type/dimensions are surfaced as canonical (path, value) ASSERTS,
  not fabricated into a flat field; geometry.shape is correct across SHAPE_DEFS.
- server-side canonical edits (name, diameter, a corrected shape) overlay on
  regeneration; unchanged parameters keep their original formatting.
- .fctl -> ToolSet sections -> .fctl round-trips with per-tool numbers promoted
  into canonical members and preserved.
"""
import json
from pathlib import Path

import pytest

from freecad.Smooth.mapping import (
    record_to_instance_sections, instance_to_fctb,
    fctl_to_set_sections, set_to_fctl,
    fctb_record_id, fctl_record_id,
    parse_quantity, format_quantity,
)

BITS = Path(__file__).parent / "fixtures" / "bits"
LIBS = Path(__file__).parent / "fixtures" / "libraries"
ALL_BITS = sorted(BITS.glob("*.fctb"))


# ---------------------------------------------------------------------------
# Helpers: build the sectioned record a server would return from what a client
# sends (assert (path,value) tuples become provenance-tagged canonical Fields).
# ---------------------------------------------------------------------------

def field(value, source="asserted:freecad", unit=None):
    f = {"value": value, "source": source}
    if unit is not None:
        f["unit"] = unit
    return f


def asserts_to_canonical(asserts):
    """Fold (path, value) asserts into a nested canonical-with-Fields dict."""
    canonical = {}
    for path, value in asserts:
        parts = path.split(".")
        node = canonical
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = field(value)
    return canonical


def fake_instance(sections, record_id="rec-1", version=1):
    """The ToolInstanceRecord the server would return for these sections."""
    return {
        "internal": {"id": record_id, "version": version,
                     "created_at": "t0", "updated_at": "t0"},
        "canonical": asserts_to_canonical(sections.asserts),
        "clients": {"freecad": {
            "client_version": "0.2.0",
            "client_item_id": sections.client_item_id,
            "created_at": "t0", "updated_at": "t0",
            "data": sections.data,
        }},
    }


def fake_set(sections, record_id="set-1", version=1):
    """The ToolSet the server would return; member numbers become Fields."""
    members = [{"tool_record_id": m["tool_record_id"],
                "number": field(m["number"], source="asserted:freecad")}
               for m in sections.members]
    canonical = asserts_to_canonical(sections.asserts)
    canonical["members"] = members
    return {
        "internal": {"id": record_id, "version": version,
                     "created_at": "t0", "updated_at": "t0"},
        "canonical": canonical,
        "clients": {"freecad": {
            "client_version": "0.2.0",
            "client_item_id": sections.client_item_id,
            "created_at": "t0", "updated_at": "t0",
            "data": sections.data,
        }},
    }


# ---------------------------------------------------------------------------
# Quantity helpers
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_parse_and_format_quantity():
    assert parse_quantity("6.00 mm") == (6.0, "mm")
    assert parse_quantity("3.175 mm") == (3.175, "mm")
    assert parse_quantity("60.00 °") == (60.0, "°")
    assert parse_quantity("Forward") == (None, None)
    assert format_quantity(6.0, "mm") == "6.00 mm"
    assert format_quantity(3.175, "mm") == "3.175 mm"


# ---------------------------------------------------------------------------
# .fctb <-> ToolInstanceRecord
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("path", ALL_BITS, ids=lambda p: p.stem)
def test_every_fixture_bit_round_trips_lossless(path):
    """fctb -> sections -> (fake server record) -> fctb returns the identical
    document plus the additive smooth key. Covers every fixture shape type."""
    original = json.loads(path.read_text())
    sections = record_to_instance_sections(original)

    record = fake_instance(sections)
    regenerated = instance_to_fctb(record)
    assert regenerated.pop("smooth") == {"record_id": "rec-1", "version": 1}
    assert regenerated == original


@pytest.mark.unit
def test_lossless_fctb_lives_in_client_data_not_canonical():
    """The full .fctb is opaque client data; canonical carries only the
    declared facts, each provenance-tagged."""
    doc = json.loads((BITS / "end_mill_6.0mm_2f.fctb").read_text())
    sections = record_to_instance_sections(doc)
    assert sections.data == {"fctb": doc}            # verbatim, lossless
    record = fake_instance(sections)
    assert record["clients"]["freecad"]["data"]["fctb"] == doc
    # canonical never holds the raw fctb; only tagged Fields
    assert "fctb" not in record["canonical"]
    assert record["canonical"]["name"]["source"] == "asserted:freecad"


@pytest.mark.unit
def test_unknown_keys_survive_including_presets():
    """The additive F&S presets key (FreeCAD PR #30078) — and any other
    unknown key — survives the round trip verbatim. The M3 preset carrier."""
    doc = json.loads((BITS / "end_mill_6.0mm_2f.fctb").read_text())
    doc["presets"] = [{"name": "alu-6061", "surface_speed": 400, "chipload": 0.05}]
    doc["somebody-elses-key"] = {"nested": [1, 2, 3]}

    sections = record_to_instance_sections(doc)
    regenerated = instance_to_fctb(fake_instance(sections))
    assert regenerated["presets"] == doc["presets"]
    assert regenerated["somebody-elses-key"] == doc["somebody-elses-key"]


@pytest.mark.unit
def test_canonical_facts_become_asserts():
    """Name, shape, and each present dimension are surfaced as (path, value)
    asserts — the canonical facts FreeCAD declares through the assert door."""
    doc = json.loads((BITS / "end_mill_6.0mm_2f.fctb").read_text())
    sections = record_to_instance_sections(doc)
    a = dict(sections.asserts)
    assert a["name"] == "End Mill 6.0mm 2F"
    assert a["geometry.shape"] == "endmill"
    assert a["geometry.diameter"] == 6.0
    assert a["geometry.flutes"] == 2
    assert a["geometry.cutting_edge_height"] == 14.0
    assert a["geometry.length"] == 50.0
    assert sections.client_item_id == "end_mill_6.0mm_2f"   # the .fctb id


@pytest.mark.unit
def test_shape_assert_omitted_when_indeterminate_never_fabricated():
    """A shapeless, name-uninformative record asserts NO shape — honestly
    unknown, never a fabricated 'endmill' (the bug the schema kills)."""
    doc = {"name": "ER11 Collet", "parameter": {}}
    sections = record_to_instance_sections(doc)
    assert all(path != "geometry.shape" for path, _ in sections.asserts)


@pytest.mark.unit
def test_shape_guessed_from_name_when_file_silent():
    doc = {"name": "30 Deg. V-Bit", "parameter": {}}
    a = dict(record_to_instance_sections(doc).asserts)
    assert a["geometry.shape"] == "vbit"


@pytest.mark.unit
def test_smooth_identity_key_is_read_then_stripped():
    """A re-exported bit's server id is read for UPDATE-vs-CREATE; the stored
    client data is clean of the plumbing key."""
    doc = json.loads((BITS / "drill_5.0mm.fctb").read_text())
    doc["smooth"] = {"record_id": "rec-99", "version": 3}
    assert fctb_record_id(doc) == "rec-99"
    sections = record_to_instance_sections(doc)
    assert "smooth" not in sections.data["fctb"]


@pytest.mark.unit
def test_server_side_edits_overlay_on_regeneration():
    """Name and diameter changed on the server appear in the file; an
    unchanged parameter keeps its original string verbatim."""
    doc = json.loads((BITS / "end_mill_6.0mm_2f.fctb").read_text())
    sections = record_to_instance_sections(doc)
    record = fake_instance(sections, version=2)
    record["canonical"]["name"] = field("renamed on server")
    record["canonical"]["geometry"]["diameter"] = field(6.35, unit="mm")

    regenerated = instance_to_fctb(record)
    assert regenerated["name"] == "renamed on server"
    assert regenerated["parameter"]["Diameter"] == "6.35 mm"
    # untouched values keep original formatting
    assert regenerated["parameter"]["Length"] == doc["parameter"]["Length"]


@pytest.mark.unit
def test_record_without_freecad_section_gets_minimal_doc():
    """A record created elsewhere (curl, importer) — no clients.freecad.data —
    still regenerates a valid .fctb from canonical geometry."""
    record = {
        "internal": {"id": "rec-7", "version": 1},
        "canonical": {
            "name": field('1/4" downcut'),
            "geometry": {"shape": field("endmill"),
                         "diameter": field(6.35, unit="mm"),
                         "flutes": field(2)},
        },
        "clients": {},
    }
    doc = instance_to_fctb(record)
    assert doc["name"] == '1/4" downcut'
    assert doc["shape"] == "endmill.fcstd"
    assert doc["shape-type"] == "Endmill"
    assert doc["parameter"]["Diameter"] == "6.35 mm"
    assert doc["parameter"]["Flutes"] == 2
    assert doc["smooth"]["record_id"] == "rec-7"


# -- Shape correctness across SHAPE_DEFS --------------------------------------

@pytest.mark.unit
def test_synthesized_tool_uses_authoritative_shape_file_and_type():
    """A synthesized bit (no base doc) must name the exact shape FILE and TYPE
    FreeCAD's models define — type+schema resolve from the file, so a wrong one
    collapses to a generic endmill. Covers the irregular names."""
    from freecad.Smooth.mapping import SHAPE_DEFS
    cases = {"vbit": ("v-bit.fcstd", "VBit"),
             "threadmill": ("thread-mill.fcstd", "ThreadMill"),
             "taperedballnose": ("taperedballnose.fcstd", "TaperedBallNose"),
             "probe": ("probe.fcstd", "Probe"),
             "reamer": ("reamer.fcstd", "Reamer"),
             "dovetail": ("dovetail.fcstd", "Dovetail")}
    for key, (shape_file, shape_type) in cases.items():
        assert SHAPE_DEFS[key] == (shape_file, shape_type)
        record = {
            "internal": {"id": "r", "version": 1},
            "canonical": {"name": field("x"),
                          "geometry": {"shape": field(key),
                                       "diameter": field(6.0, unit="mm")}},
            "clients": {},
        }
        doc = instance_to_fctb(record)
        assert doc["shape"] == shape_file
        assert doc["shape-type"] == shape_type
        assert doc["parameter"]["Diameter"].startswith("6")  # geometry overlaid


@pytest.mark.unit
def test_all_freecad_shapes_are_offered():
    """The picker covers every shape model FreeCAD ships."""
    from freecad.Smooth.mapping import FREECAD_SHAPES
    assert set(FREECAD_SHAPES) >= {
        "endmill", "ballend", "bullnose", "chamfer", "dovetail", "drill",
        "probe", "radius", "reamer", "slittingsaw", "tap", "taperedballnose",
        "threadmill", "vbit"}


@pytest.mark.unit
def test_chosen_shape_on_import_overrides_the_file():
    """The user's import-time type choice wins over the file's shape-type
    (the 'set the type on import' picker)."""
    doc = {"name": "Mystery 6mm", "shape": "endmill.fcstd",
           "shape-type": "Endmill", "parameter": {"Diameter": "6.00 mm"}}
    a = dict(record_to_instance_sections(doc, shape="ballend").asserts)
    assert a["geometry.shape"] == "ballend"


@pytest.mark.unit
def test_corrected_shape_rebuilds_discarding_wrong_base():
    """The pollution case: a Probe stored as an endmill stub. When canonical
    shape is corrected to 'probe', the endmill base is discarded and the bit
    is rebuilt with the authoritative probe shape file/type."""
    base = {"version": 2, "name": "Probe", "shape": "endmill.fcstd",
            "shape-type": "Endmill", "attribute": {},
            "parameter": {"Diameter": "3.00 mm"}}
    record = {
        "internal": {"id": "r", "version": 1},
        "canonical": {"name": field("Probe"),
                      "geometry": {"shape": field("probe"),
                                   "diameter": field(3.0, unit="mm")}},
        "clients": {"freecad": {"client_version": "0.2.0",
                                "data": {"fctb": base}}},
    }
    doc = instance_to_fctb(record)
    assert doc["shape"] == "probe.fcstd"      # rebuilt; endmill base discarded
    assert doc["shape-type"] == "Probe"


@pytest.mark.unit
def test_matching_shape_preserves_verbatim_base():
    """When canonical shape matches the base's type, the verbatim document is
    preserved (lossless), including shape-specific parameters."""
    base = {"version": 2, "name": "x", "shape": "v-bit.fcstd", "shape-type": "VBit",
            "parameter": {"CuttingEdgeAngle": "30 °"}}
    record = {
        "internal": {"id": "r", "version": 1},
        "canonical": {"name": field("x"),
                      "geometry": {"shape": field("vbit")}},
        "clients": {"freecad": {"client_version": "0.2.0",
                                "data": {"fctb": base}}},
    }
    doc = instance_to_fctb(record)
    assert doc["shape"] == "v-bit.fcstd"
    assert doc["parameter"]["CuttingEdgeAngle"] == "30 °"   # untouched


# ---------------------------------------------------------------------------
# .fctl <-> ToolSet
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_fctl_round_trip_preserves_numbers_and_order():
    fctl = json.loads(next(LIBS.glob("*.fctl")).read_text())
    paths = [t["path"] for t in fctl["tools"]]
    id_by_path = {p: "rec-%d" % i for i, p in enumerate(paths)}

    sections = fctl_to_set_sections(fctl, id_by_path)
    assert sections.unresolved == []
    assert dict(sections.asserts)["name"] == fctl["label"]
    assert len(sections.members) == len(fctl["tools"])
    # the FreeCAD-specific bits are opaque client data, not canonical
    assert sections.data == {"fctl_label": fctl["label"],
                             "version": fctl["version"]}

    record = fake_set(sections, record_id="set-1")
    path_by_id = {v: k for k, v in id_by_path.items()}
    regenerated, unresolved = set_to_fctl(record, path_by_id)
    assert unresolved == []
    assert regenerated.pop("smooth") == {"record_id": "set-1", "version": 1}
    assert regenerated == fctl


@pytest.mark.unit
def test_numbers_are_promoted_into_canonical_members():
    """Per-tool numbers leave the client section and become canonical
    membership positions (§7.4) — shared truth, not a private copy."""
    fctl = {"label": "default", "version": 1,
            "tools": [{"nr": 11, "path": "a.fctb"}, {"nr": 12, "path": "b.fctb"}]}
    sections = fctl_to_set_sections(fctl, {"a.fctb": "rec-1", "b.fctb": "rec-2"})
    assert sections.members == [{"tool_record_id": "rec-1", "number": 11},
                                {"tool_record_id": "rec-2", "number": 12}]
    assert "numbers" not in sections.data          # not in the client section


@pytest.mark.unit
def test_fctl_unresolved_paths_are_reported_not_dropped():
    fctl = {"label": "x", "version": 1,
            "tools": [{"nr": 1, "path": "known.fctb"},
                      {"nr": 2, "path": "mystery.fctb"}]}
    sections = fctl_to_set_sections(fctl, {"known.fctb": "rec-1"})
    assert sections.unresolved == ["mystery.fctb"]
    assert [m["tool_record_id"] for m in sections.members] == ["rec-1"]


@pytest.mark.unit
def test_member_without_number_gets_next_free_number():
    record = {
        "internal": {"id": "set-1", "version": 2},
        "canonical": {"name": field("default"),
                      "members": [
                          {"tool_record_id": "rec-1",
                           "number": field(11, source="asserted:freecad")},
                          {"tool_record_id": "rec-new",
                           "number": field(None, source="unknown")}]},
        "clients": {"freecad": {"client_version": "0.2.0",
                                "data": {"fctl_label": "default", "version": 1}}},
    }
    doc, unresolved = set_to_fctl(
        record, {"rec-1": "a.fctb", "rec-new": "b.fctb"})
    assert unresolved == []
    assert doc["tools"] == [{"nr": 11, "path": "a.fctb"},
                            {"nr": 12, "path": "b.fctb"}]


@pytest.mark.unit
def test_set_member_without_known_path_is_reported():
    record = {
        "internal": {"id": "set-1", "version": 1},
        "canonical": {"name": field("default"),
                      "members": [
                          {"tool_record_id": "rec-1",
                           "number": field(1, source="asserted:freecad")},
                          {"tool_record_id": "rec-x",
                           "number": field(2, source="asserted:freecad")}]},
        "clients": {"freecad": {"client_version": "0.2.0",
                                "data": {"fctl_label": "default", "version": 1}}},
    }
    doc, unresolved = set_to_fctl(record, {"rec-1": "a.fctb"})
    assert unresolved == ["rec-x"]
    assert doc["tools"] == [{"nr": 1, "path": "a.fctb"}]


@pytest.mark.unit
def test_fctl_identity_key_round_trips():
    fctl = {"label": "x", "version": 1, "tools": [],
            "smooth": {"record_id": "set-42", "version": 7}}
    assert fctl_record_id(fctl) == "set-42"
    sections = fctl_to_set_sections(fctl, {})
    assert "smooth" not in sections.data
