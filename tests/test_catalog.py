# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the M2 catalog flow: ``sync.create_tool_from_catalog`` (smooth-core's
catalog->instance feature, FreeCAD half).

The contract under test, exercised against the in-memory sectioned ``FakeServer``
(conftest), which seeds one ToolCatalogRecord:

- creating from a catalog record mints an UNBOUND server instance AND materializes
  a local ``.fctb`` in ``Bit/`` linked to that NEW instance (smooth.record_id);
- the local tool is pre-filled from the CATALOG's nominal geometry (the instance's
  own measured geometry is empty by M2 design);
- the sync journal learns the record so the next sync updates, not re-creates;
- a second create from the same catalog produces a DISTINCT file (dedup suffix)
  and a distinct server instance;
- a name override is honored.
"""
import json
from pathlib import Path

import pytest

from freecad.Smooth import sync
from conftest import FakeServer


def read(p):
    return json.loads(Path(p).read_text())


@pytest.mark.unit
def test_create_tool_from_catalog_writes_linked_fctb(tools_dir):
    server = FakeServer()
    catalog = server.list_catalog_records()[0]

    result = sync.create_tool_from_catalog(str(tools_dir), server, catalog)

    # (1) an instance now exists on the server with empty canonical geometry
    #     (unmeasured — nominal geometry is reachable through the catalog link),
    #     and FreeCAD's client section IS written so the new tool lands SYNCED
    #     (the sync base) rather than as a no-base "conflict" on the next plan.
    inst_id = result["instance"]["internal"]["id"]
    assert inst_id in server.instances
    assert server.instances[inst_id]["canonical"]["geometry"] == {}  # unmeasured
    assert "freecad" in server.instances[inst_id]["clients"]         # sync base written
    plan = sync.plan_sync(str(tools_dir), server)
    item = next(i for i in plan["items"]
                if (i.get("record") or {}).get("internal", {}).get("id") == inst_id)
    assert item["action"] == "unchanged"                             # synced, not a conflict

    # (2) the .fctb landed in Bit/, linked to the NEW instance, with the
    #     catalog's nominal geometry (not the instance's empty geometry)
    path = Path(result["path"])
    assert path.parent.name == "Bit" and path.exists()
    doc = read(path)
    assert doc["smooth"]["record_id"] == inst_id
    assert doc["shape"] == "endmill.fcstd"
    assert doc["parameter"]["Diameter"] == "6.35 mm"
    assert doc["parameter"]["Flutes"] == 2

    # (3) the sync journal tracks it (so the next sync UPDATES, not re-creates)
    state = read(tools_dir / sync.STATE_BASENAME)
    assert state["records"][inst_id] == result["basename"]


@pytest.mark.unit
def test_second_create_from_same_catalog_is_distinct(tools_dir):
    """Two creates from the same catalog record yield two distinct files and two
    distinct server instances (the dedup suffix, mirroring pull_bit)."""
    server = FakeServer()
    catalog = server.list_catalog_records()[0]

    first = sync.create_tool_from_catalog(str(tools_dir), server, catalog)
    second = sync.create_tool_from_catalog(str(tools_dir), server, catalog)

    assert first["path"] != second["path"]
    assert first["basename"] != second["basename"]
    assert Path(first["path"]).exists() and Path(second["path"]).exists()
    assert (first["instance"]["internal"]["id"]
            != second["instance"]["internal"]["id"])
    assert len(server.instances) == 2


@pytest.mark.unit
def test_create_tool_from_catalog_honors_name_override(tools_dir):
    server = FakeServer()
    catalog = server.list_catalog_records()[0]

    result = sync.create_tool_from_catalog(str(tools_dir), server, catalog,
                                           name="Special lot 7")
    doc = read(result["path"])
    assert doc["name"] == "Special lot 7"
    assert doc["parameter"]["Diameter"] == "6.35 mm"   # geometry still catalog's


@pytest.mark.unit
def test_created_instance_is_tracked_and_matched_by_next_plan(tools_dir):
    """The new instance is journaled and re-adopted by the next plan via the
    written-back smooth.record_id — it is matched to its file (not seen as a
    duplicate 'new_local' or a phantom 'deleted_local'), so a later sync acts on
    the one record. (Its geometry is reconciled like any other bit; M2 does not
    push the instance's measured geometry.)"""
    server = FakeServer()
    catalog = server.list_catalog_records()[0]
    result = sync.create_tool_from_catalog(str(tools_dir), server, catalog)
    inst_id = result["instance"]["internal"]["id"]

    plan = sync.plan_sync(str(tools_dir), server)
    item = next(i for i in plan["items"]
                if (i.get("record") or {}).get("internal", {}).get("id") == inst_id)
    assert item["path"] is not None                    # matched to its file
    assert item["action"] not in ("new_local", "new_server", "deleted_local")
