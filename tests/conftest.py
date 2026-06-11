# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""Shared fixtures: the in-memory fake server and a populated tools dir."""
import json
import shutil
from pathlib import Path

import pytest

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

    def delete_records(self, ids):
        n = 0
        for rid in ids:
            if rid in self.records:
                del self.records[rid]; n += 1
        return {"success_count": n, "errors": [], "items": []}

    def delete_libraries(self, ids):
        n = 0
        for lid in ids:
            if lid in self.libraries:
                del self.libraries[lid]; n += 1
        return {"success_count": n, "errors": [], "items": []}

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
