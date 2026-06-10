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
import shutil
from pathlib import Path

import pytest

from freecad.Smooth import sync

FIXTURES = Path(__file__).parent / "fixtures"


class FakeServer:
    """In-memory stand-in honoring the facade bulk contract."""

    def __init__(self):
        self.records = {}
        self.libraries = {}
        self._n = 0

    def _next(self, prefix):
        self._n += 1
        return "%s-%d" % (prefix, self._n)

    def list_records(self):
        return list(self.records.values())

    def create_records(self, items):
        out = []
        for item in items:
            rid = self._next("rec")
            self.records[rid] = {**item, "id": rid, "version": 1}
            out.append(self.records[rid])
        return {"success_count": len(out), "errors": [], "items": out}

    def update_records(self, items):
        out, errors = [], []
        for i, item in enumerate(items):
            current = self.records.get(item["id"])
            if current is None:
                errors.append({"index": i, "message": "not found"})
                continue
            if current["version"] != item["version"]:
                errors.append({"index": i, "message": "Version conflict"})
                continue
            current.update({k: v for k, v in item.items() if k != "version"})
            current["version"] += 1
            out.append(current)
        return {"success_count": len(out), "errors": errors, "items": out}

    def list_libraries(self):
        return list(self.libraries.values())

    def create_libraries(self, items):
        out = []
        for item in items:
            lid = self._next("lib")
            self.libraries[lid] = {**item, "id": lid, "version": 1}
            out.append(self.libraries[lid])
        return {"success_count": len(out), "errors": [], "items": out}

    def update_libraries(self, items):
        out, errors = [], []
        for i, item in enumerate(items):
            current = self.libraries.get(item["id"])
            if current is None:
                errors.append({"index": i, "message": "not found"})
                continue
            current.update({k: v for k, v in item.items() if k != "version"})
            current["version"] += 1
            out.append(current)
        return {"success_count": len(out), "errors": errors, "items": out}


@pytest.fixture
def tools_dir(tmp_path):
    """A FreeCAD-style Tools dir with 3 bits and a small library."""
    bit_dir = tmp_path / "Bit"
    lib_dir = tmp_path / "Library"
    bit_dir.mkdir()
    lib_dir.mkdir()
    for name in ["drill_5.0mm.fctb", "end_mill_6.0mm_2f.fctb", "probe.fctb"]:
        shutil.copy(FIXTURES / "bits" / name, bit_dir / name)
    (lib_dir / "default.fctl").write_text(json.dumps({
        "label": "default", "version": 1,
        "tools": [{"nr": 1, "path": "drill_5.0mm.fctb"},
                  {"nr": 2, "path": "end_mill_6.0mm_2f.fctb"}],
    }))
    return tmp_path


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
    assert fctl["smooth"]["library_id"] in server.libraries

    library = server.libraries[fctl["smooth"]["library_id"]]
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
    assert len(server.libraries) == 1
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
    library = list(server.libraries.values())[0]
    assert len(library["tool_record_ids"]) == 2
