# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for import + lossless round-trip (smooth-freecad#6).

The contract under test:
- export -> import is a no-op on disk (semantic round-trip equality)
- server-side edits land in the files; local-only edits are kept
  ("pending export"); both-changed is a reported CONFLICT, never an
  overwrite (D5's no-silent-clobber on the CAM side)
- records born on the server materialize as new valid .fctb files;
  server-side library membership changes rewrite the .fctl
"""
import json
from pathlib import Path

import pytest

from freecad.Smooth import sync
from conftest import FakeServer


def read(p):
    return json.loads(Path(p).read_text())


@pytest.mark.unit
def test_export_then_import_is_noop(tools_dir):
    """The acceptance round trip: nothing on disk changes semantically."""
    server = FakeServer()
    sync.export_tools(str(tools_dir), server)
    before = {p.name: read(p) for p in (tools_dir / "Bit").glob("*.fctb")}
    before.update({p.name: read(p) for p in (tools_dir / "Library").glob("*.fctl")})

    summary = sync.import_tools(str(tools_dir), server)
    assert summary["errors"] == [] and summary["conflicts"] == []
    assert summary["written"] == 0
    assert summary["unchanged"] == 4

    after = {p.name: read(p) for p in (tools_dir / "Bit").glob("*.fctb")}
    after.update({p.name: read(p) for p in (tools_dir / "Library").glob("*.fctl")})
    assert after == before


@pytest.mark.unit
def test_server_edit_lands_in_file(tools_dir):
    server = FakeServer()
    sync.export_tools(str(tools_dir), server)
    path = tools_dir / "Bit" / "drill_5.0mm.fctb"
    rid = read(path)["smooth"]["record_id"]
    server.records[rid]["name"] = "renamed on server"
    server.records[rid]["version"] += 1

    summary = sync.import_tools(str(tools_dir), server)
    assert summary["written"] == 1 and summary["conflicts"] == []
    doc = read(path)
    assert doc["name"] == "renamed on server"
    assert doc["smooth"]["record_id"] == rid  # identity survives the write
    # unknown-key guarantee: parameters untouched
    assert doc["parameter"]["Diameter"] == "5.00 mm"


@pytest.mark.unit
def test_local_edit_is_kept_pending_export(tools_dir):
    server = FakeServer()
    sync.export_tools(str(tools_dir), server)
    path = tools_dir / "Bit" / "drill_5.0mm.fctb"
    doc = read(path)
    doc["parameter"]["Diameter"] = "5.30 mm"
    path.write_text(json.dumps(doc))

    summary = sync.import_tools(str(tools_dir), server)
    assert summary["pending_export"] == 1
    assert read(path)["parameter"]["Diameter"] == "5.30 mm"  # untouched


@pytest.mark.unit
def test_both_changed_is_conflict_never_overwritten(tools_dir):
    server = FakeServer()
    sync.export_tools(str(tools_dir), server)
    path = tools_dir / "Bit" / "drill_5.0mm.fctb"
    doc = read(path)
    rid = doc["smooth"]["record_id"]
    doc["parameter"]["Diameter"] = "5.30 mm"      # local edit
    path.write_text(json.dumps(doc))
    server.records[rid]["name"] = "server rename"  # server edit
    server.records[rid]["version"] += 1

    summary = sync.import_tools(str(tools_dir), server)
    assert len(summary["conflicts"]) == 1
    assert "drill_5.0mm" in summary["conflicts"][0]
    kept = read(path)
    assert kept["parameter"]["Diameter"] == "5.30 mm"   # local preserved
    assert kept["name"] != "server rename"


@pytest.mark.unit
def test_server_born_record_materializes_as_new_file(tools_dir):
    server = FakeServer()
    sync.export_tools(str(tools_dir), server)
    server.create_records([{
        "name": '1/4" downcut',
        "geometry": {"shape": "endmill", "diameter": 6.35,
                     "diameter_unit": "mm", "flutes": 2},
    }])

    summary = sync.import_tools(str(tools_dir), server)
    assert summary["written"] == 1
    new_files = [p for p in (tools_dir / "Bit").glob("*.fctb")
                 if "downcut" in p.name]
    assert len(new_files) == 1
    doc = read(new_files[0])
    assert doc["parameter"]["Diameter"] == "6.35 mm"
    assert "smooth" in doc  # adopted: next export updates, not creates


@pytest.mark.unit
def test_server_membership_change_rewrites_fctl(tools_dir):
    server = FakeServer()
    sync.export_tools(str(tools_dir), server)
    library = list(server.libraries.values())[0]
    probe_rid = read(tools_dir / "Bit" / "probe.fctb")["smooth"]["record_id"]
    library["tool_record_ids"] = library["tool_record_ids"] + [probe_rid]
    library["version"] += 1

    summary = sync.import_tools(str(tools_dir), server)
    assert summary["conflicts"] == []
    fctl = read(tools_dir / "Library" / "default.fctl")
    assert len(fctl["tools"]) == 3
    assert any(t["path"] == "probe.fctb" for t in fctl["tools"])
    # original numbers preserved, new member got the next free nr
    numbers = sorted(t["nr"] for t in fctl["tools"])
    assert numbers == [1, 2, 3]


@pytest.mark.unit
def test_import_after_editor_wipe_rebinds_by_fctb_id(tools_dir):
    """Editor destroyed the smooth key locally; import still matches the
    file by fctb id and refreshes the identity instead of duplicating."""
    server = FakeServer()
    sync.export_tools(str(tools_dir), server)
    path = tools_dir / "Bit" / "probe.fctb"
    doc = read(path)
    doc.pop("smooth")
    path.write_text(json.dumps(doc))

    summary = sync.import_tools(str(tools_dir), server)
    assert summary["conflicts"] == [] and summary["errors"] == []
    # no duplicate materialized under ANY name
    assert len(list((tools_dir / "Bit").glob("*.fctb"))) == 3
    assert "smooth" in read(path)
