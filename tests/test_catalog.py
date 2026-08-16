# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the M2 catalog flow: ``sync.create_tool_from_catalog`` (loobric-server's
catalog->instance feature, FreeCAD half).

The contract under test, exercised against the in-memory sectioned ``FakeServer``
(conftest), which seeds one ToolCatalogRecord:

- creating from a catalog record mints an UNBOUND server instance AND materializes
  a local ``.fctb`` in ``Bit/`` linked to that NEW instance (loobric.record_id);
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

from freecad.Loobric import sync, viewmodel
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
    assert doc["loobric"]["record_id"] == inst_id
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
    written-back loobric.record_id — it is matched to its file (not seen as a
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


# ---------------------------------------------------------------------------
# The grouped catalog browse (server >= 0.14.0): viewmodel.catalog_tree
# ---------------------------------------------------------------------------

def _seed_records(server, n=3):
    out = []
    for i in range(n):
        rec = server._blank(server._next("cat"))
        rec["canonical"] = {"name": server._field("rec %d" % i),
                            "manufacturer": server._field("Maker"),
                            "product_code": server._field("PC-%d" % i)}
        server.catalogs[rec["internal"]["id"]] = rec
        out.append(rec["internal"]["id"])
    return out


def test_catalog_tree_groups_and_uncataloged():
    server = FakeServer()
    r1, r2, r3 = _seed_records(server)
    g = server.create_catalog("Harvey 2026")
    server.set_catalog_members(g["internal"]["id"], [r1, r2])
    tree = viewmodel.catalog_tree(server.list_catalog_records(),
                                  server.list_catalogs())
    names = [n["name"] for n in tree]
    assert names[0] == "Harvey 2026"
    assert names[-1] == "Uncataloged"
    assert [r["id"] for r in tree[0]["rows"]] == [r1, r2]
    uncat = [r["id"] for r in tree[-1]["rows"]]
    assert r3 in uncat and r1 not in uncat


def test_catalog_tree_multi_membership_shows_record_twice():
    # Membership is organization, never identity — no deduping across groups.
    server = FakeServer()
    (r1,) = _seed_records(server, 1)
    a = server.create_catalog("import")
    b = server.create_catalog("curated")
    server.set_catalog_members(a["internal"]["id"], [r1])
    server.set_catalog_members(b["internal"]["id"], [r1])
    tree = viewmodel.catalog_tree(server.list_catalog_records(),
                                  server.list_catalogs())
    holders = [n["name"] for n in tree if any(r["id"] == r1 for r in n["rows"])]
    assert holders == ["curated", "import"]            # sorted by name


def test_catalog_tree_degrades_flat_without_groups():
    # Old server / no catalogs yet: one anonymous node, no folder chrome.
    server = FakeServer()
    _seed_records(server, 2)
    tree = viewmodel.catalog_tree(server.list_catalog_records(), [])
    assert len(tree) == 1 and tree[0]["name"] is None
    assert len(tree[0]["rows"]) >= 2


def test_catalog_tree_stale_member_ids_are_skipped():
    # A member id whose record vanished renders nothing, never a ghost row.
    server = FakeServer()
    (r1,) = _seed_records(server, 1)
    g = server.create_catalog("holey")
    server.set_catalog_members(g["internal"]["id"], [r1, "ghost"])
    tree = viewmodel.catalog_tree(server.list_catalog_records(),
                                  server.list_catalogs())
    assert [r["id"] for r in tree[0]["rows"]] == [r1]
