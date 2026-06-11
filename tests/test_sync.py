# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for export sync orchestration (smooth-freecad#5).

The contract under test (against an in-memory fake server):
- First export creates records + libraries and writes server ids back
  into the files (additive 'smooth' key)
- Re-export UPDATES in place — never duplicates (the production lesson)
- Bits export before libraries; membership resolves through fresh ids
- Per-item server errors surface in the summary, never silently dropped
"""
import json
from pathlib import Path

import pytest

from freecad.Smooth import sync

from conftest import FakeServer


@pytest.mark.unit
def test_first_export_creates_and_writes_back_ids(tools_dir):
    server = FakeServer()
    summary = sync.export_tools(str(tools_dir), server)

    assert summary["errors"] == []
    assert summary["created"] == 4  # 3 bits + 1 library
    assert summary["updated"] == 0

    drill = json.loads((tools_dir / "Bit" / "drill_5.0mm.fctb").read_text())
    assert drill["smooth"]["record_id"] in server.records
    fctl = json.loads((tools_dir / "Library" / "default.fctl").read_text())
    assert fctl["smooth"]["tool_set_id"] in server.tool_sets

    library = server.tool_sets[fctl["smooth"]["tool_set_id"]]
    assert len(library["tool_record_ids"]) == 2
    assert library["extra"]["freecad"]["numbers"][library["tool_record_ids"][0]] == 1


@pytest.mark.unit
def test_reexport_updates_never_duplicates(tools_dir):
    """The production lesson, pinned: three exports in a row leave exactly
    one server object per file."""
    server = FakeServer()
    sync.export_tools(str(tools_dir), server)
    second = sync.export_tools(str(tools_dir), server)
    third = sync.export_tools(str(tools_dir), server)

    assert len(server.records) == 3
    assert len(server.tool_sets) == 1
    assert second["created"] == 0 and second["updated"] == 4
    assert third["created"] == 0 and third["updated"] == 4


@pytest.mark.unit
def test_local_edit_reaches_server_on_reexport(tools_dir):
    server = FakeServer()
    sync.export_tools(str(tools_dir), server)

    path = tools_dir / "Bit" / "drill_5.0mm.fctb"
    doc = json.loads(path.read_text())
    doc["parameter"]["Diameter"] = "5.10 mm"
    path.write_text(json.dumps(doc))

    sync.export_tools(str(tools_dir), server)
    record = server.records[doc["smooth"]["record_id"]]
    assert record["geometry"]["diameter"] == 5.1
    assert record["extra"]["freecad"]["fctb"]["parameter"]["Diameter"] == "5.10 mm"


@pytest.mark.unit
def test_deleted_server_record_is_recreated(tools_dir):
    """A stale id in the file (record deleted server-side) falls back to
    create instead of erroring forever."""
    server = FakeServer()
    sync.export_tools(str(tools_dir), server)
    victim_id = json.loads(
        (tools_dir / "Bit" / "probe.fctb").read_text()
    )["smooth"]["record_id"]
    del server.records[victim_id]

    summary = sync.export_tools(str(tools_dir), server)
    assert summary["errors"] == []
    assert summary["created"] == 1 and summary["updated"] == 3
    new_id = json.loads(
        (tools_dir / "Bit" / "probe.fctb").read_text()
    )["smooth"]["record_id"]
    assert new_id != victim_id and new_id in server.records


@pytest.mark.unit
def test_unreadable_bit_is_reported_and_rest_proceed(tools_dir):
    (tools_dir / "Bit" / "broken.fctb").write_text("{not json")
    server = FakeServer()
    summary = sync.export_tools(str(tools_dir), server)
    assert any("broken.fctb" in e for e in summary["errors"])
    assert summary["created"] == 4  # the rest exported fine


@pytest.mark.unit
def test_library_member_without_record_is_reported(tools_dir):
    fctl_path = tools_dir / "Library" / "default.fctl"
    doc = json.loads(fctl_path.read_text())
    doc["tools"].append({"nr": 9, "path": "ghost.fctb"})
    fctl_path.write_text(json.dumps(doc))

    server = FakeServer()
    summary = sync.export_tools(str(tools_dir), server)
    assert any("ghost.fctb" in e for e in summary["errors"])
    # library still created with the resolvable members
    library = list(server.tool_sets.values())[0]
    assert len(library["tool_record_ids"]) == 2


@pytest.mark.unit
def test_editor_wiped_smooth_key_readopts_no_duplicate(tools_dir):
    """Field finding: FreeCAD's ToolBit editor drops unknown top-level keys
    on save, destroying the 'smooth' identity key. Export must re-adopt the
    server record by the .fctb 'id' field (exact match on FreeCAD's own
    stable identifier, stored verbatim server-side) instead of duplicating."""
    server = FakeServer()
    sync.export_tools(str(tools_dir), server)
    assert len(server.records) == 3

    # simulate the editor: rewrite the file without the smooth key
    path = tools_dir / "Bit" / "drill_5.0mm.fctb"
    doc = json.loads(path.read_text())
    old_id = doc.pop("smooth")["record_id"]
    doc["parameter"]["Diameter"] = "5.20 mm"  # the edit the user made
    path.write_text(json.dumps(doc))

    summary = sync.export_tools(str(tools_dir), server)
    assert summary["errors"] == []
    assert len(server.records) == 3          # no duplicate
    assert summary["created"] == 0 and summary["updated"] == 4
    assert server.records[old_id]["geometry"]["diameter"] == 5.2
    # identity key restored in the file
    restored = json.loads(path.read_text())
    assert restored["smooth"]["record_id"] == old_id


@pytest.mark.unit
def test_ambiguous_fctb_id_is_error_not_guess(tools_dir):
    """If two server records claim the same .fctb id (shouldn't happen, but
    data is data), re-adoption refuses and reports - never guesses."""
    server = FakeServer()
    sync.export_tools(str(tools_dir), server)

    # forge a second record with the same embedded fctb id as the drill
    drill_doc = json.loads((tools_dir / "Bit" / "drill_5.0mm.fctb").read_text())
    fctb_id = drill_doc["extra"]["freecad"]["fctb"]["id"] if "extra" in drill_doc else "drill_5.0mm"
    clone = {"name": "impostor", "extra": {"freecad": {"fctb": {"id": "drill_5.0mm"}}}}
    server.create_records([clone])

    # wipe the smooth key so export must re-adopt
    doc = json.loads((tools_dir / "Bit" / "drill_5.0mm.fctb").read_text())
    doc.pop("smooth", None)
    (tools_dir / "Bit" / "drill_5.0mm.fctb").write_text(json.dumps(doc))

    summary = sync.export_tools(str(tools_dir), server)
    assert any("drill_5.0mm" in e and "ambiguous" in e.lower() for e in summary["errors"])
