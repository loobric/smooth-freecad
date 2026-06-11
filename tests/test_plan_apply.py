# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the plan/apply sync (smooth-freecad#7).

The contract under test:
- plan_sync classifies every item correctly and touches NOTHING
- apply_sync executes only the selected decisions; everything else is
  untouched on both sides
- conflicts honor the per-item human choice: keep_local force-uploads,
  take_server rewrites the file
"""
import json
from pathlib import Path

import pytest

from freecad.Smooth import sync
from conftest import FakeServer


def read(p):
    return json.loads(Path(p).read_text())


def plan_by_key(plan):
    return {i["key"]: i for i in plan["items"]}


@pytest.mark.unit
def test_plan_is_pure_and_classifies_fresh_state(tools_dir):
    server = FakeServer()
    before = {p.name: p.read_text() for p in (tools_dir / "Bit").glob("*")}

    plan = sync.plan_sync(str(tools_dir), server)
    assert plan["errors"] == []
    actions = {i["key"]: i["action"] for i in plan["items"]}
    assert actions == {
        "bit:drill_5.0mm.fctb": "new_local",
        "bit:end_mill_6.0mm_2f.fctb": "new_local",
        "bit:probe.fctb": "new_local",
        "lib:default.fctl": "new_local",
    }
    # nothing was touched, client saw no writes
    assert {p.name: p.read_text() for p in (tools_dir / "Bit").glob("*")} == before
    assert server.records == {} and server.libraries == {}
    # membership info present
    drill = plan_by_key(plan)["bit:drill_5.0mm.fctb"]
    assert drill["library"] == "default.fctl"


@pytest.mark.unit
def test_apply_only_selected(tools_dir):
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {"bit:drill_5.0mm.fctb": "push"})
    assert summary["pushed"] == 1 and summary["errors"] == []
    assert len(server.records) == 1
    assert "smooth" not in read(tools_dir / "Bit" / "probe.fctb")  # untouched


@pytest.mark.unit
def test_full_cycle_then_unchanged(tools_dir):
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    decisions = {}
    for i in plan["items"]:
        decisions[i["key"]] = "pull" if i["action"] in ("pull", "new_server") else "push"
    summary = sync.apply_sync(str(tools_dir), server, plan, decisions)
    assert summary["errors"] == []
    assert summary["pushed"] == 4

    plan = sync.plan_sync(str(tools_dir), server)
    assert {i["action"] for i in plan["items"]} == {"unchanged"}


@pytest.mark.unit
def test_plan_classifies_push_pull_conflict_and_new_server(tools_dir):
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    sync.apply_sync(str(tools_dir), server, plan,
                    {i["key"]: "push" for i in plan["items"]})

    # local edit -> push
    p = tools_dir / "Bit" / "drill_5.0mm.fctb"
    doc = read(p); doc["parameter"]["Diameter"] = "5.10 mm"
    p.write_text(json.dumps(doc))
    # server edit -> pull
    em = next(r for r in server.records.values() if "end_mill" in r["extra"]["freecad"]["filename"])
    em["name"] = "server renamed"; em["version"] += 1
    # both -> conflict
    pp = tools_dir / "Bit" / "probe.fctb"
    pdoc = read(pp); pdoc["parameter"]["Length"] = "55.0000 mm"
    pp.write_text(json.dumps(pdoc))
    probe = next(r for r in server.records.values() if "probe" in r["extra"]["freecad"]["filename"])
    probe["name"] = "server probe"; probe["version"] += 1
    # server-born record
    server.create_records([{"name": "born on server",
                            "geometry": {"shape": "endmill", "diameter": 8.0}}])

    plan = plan_by_key(sync.plan_sync(str(tools_dir), server))
    assert plan["bit:drill_5.0mm.fctb"]["action"] == "push"
    assert plan["bit:end_mill_6.0mm_2f.fctb"]["action"] == "pull"
    assert plan["bit:probe.fctb"]["action"] == "conflict"
    server_only = [i for i in plan.values() if i["action"] == "new_server"]
    assert len(server_only) == 1 and server_only[0]["name"] == "born on server"


@pytest.mark.unit
def test_conflict_keep_local_force_uploads(tools_dir):
    server = FakeServer()
    first = sync.plan_sync(str(tools_dir), server)
    sync.apply_sync(str(tools_dir), server, first,
                    {i["key"]: "push" for i in first["items"]})
    p = tools_dir / "Bit" / "probe.fctb"
    doc = read(p); doc["parameter"]["Length"] = "55.0000 mm"
    p.write_text(json.dumps(doc))
    probe = next(r for r in server.records.values() if "probe" in r["extra"]["freecad"]["filename"])
    probe["name"] = "server probe"; probe["version"] += 1

    plan = sync.plan_sync(str(tools_dir), server)
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {"bit:probe.fctb": "push"})
    assert summary["errors"] == [] and summary["pushed"] == 1
    assert probe["extra"]["freecad"]["fctb"]["parameter"]["Length"] == "55.0000 mm"
    # local wins wholesale: server-side rename overruled by the human choice
    plan = plan_by_key(sync.plan_sync(str(tools_dir), server))
    assert plan["bit:probe.fctb"]["action"] == "unchanged"


@pytest.mark.unit
def test_conflict_take_server_rewrites_file(tools_dir):
    server = FakeServer()
    first = sync.plan_sync(str(tools_dir), server)
    sync.apply_sync(str(tools_dir), server, first,
                    {i["key"]: "push" for i in first["items"]})
    p = tools_dir / "Bit" / "probe.fctb"
    doc = read(p); doc["parameter"]["Length"] = "55.0000 mm"
    p.write_text(json.dumps(doc))
    probe = next(r for r in server.records.values() if "probe" in r["extra"]["freecad"]["filename"])
    probe["name"] = "server probe"; probe["version"] += 1

    plan = sync.plan_sync(str(tools_dir), server)
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {"bit:probe.fctb": "pull"})
    assert summary["pulled"] == 1
    after = read(p)
    assert after["name"] == "server probe"
    assert after["parameter"]["Length"] != "55.0000 mm"  # local edit discarded by choice
    plan = plan_by_key(sync.plan_sync(str(tools_dir), server))
    assert plan["bit:probe.fctb"]["action"] == "unchanged"


@pytest.mark.unit
def test_diff_attributes_changes_to_the_right_side(tools_dir):
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    sync.apply_sync(str(tools_dir), server, plan,
                    {i["key"]: "push" for i in plan["items"]})

    p = tools_dir / "Bit" / "drill_5.0mm.fctb"
    doc = read(p); doc["parameter"]["Diameter"] = "5.10 mm"
    p.write_text(json.dumps(doc))
    rec = next(r for r in server.records.values()
               if "drill" in r["extra"]["freecad"]["filename"])
    rec["name"] = "server rename"; rec["version"] += 1

    item = plan_by_key(sync.plan_sync(str(tools_dir), server))["bit:drill_5.0mm.fctb"]
    assert item["action"] == "conflict"
    by_field = {d["field"]: d for d in item["diff"]}
    assert by_field["parameter.Diameter"]["changed_by"] == "local"
    assert by_field["parameter.Diameter"]["local"] == "5.10 mm"
    assert by_field["name"]["changed_by"] == "server"
    assert by_field["name"]["server"] == "server rename"


@pytest.mark.unit
def test_direction_override_reverts_local_edit(tools_dir):
    """A locally-changed tool can be PULLED to discard the local edit -
    the user chooses direction, the classification is only a default."""
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    sync.apply_sync(str(tools_dir), server, plan,
                    {i["key"]: "push" for i in plan["items"]})
    p = tools_dir / "Bit" / "drill_5.0mm.fctb"
    doc = read(p); doc["parameter"]["Diameter"] = "5.10 mm"
    p.write_text(json.dumps(doc))

    plan = sync.plan_sync(str(tools_dir), server)
    assert plan_by_key(plan)["bit:drill_5.0mm.fctb"]["action"] == "push"
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {"bit:drill_5.0mm.fctb": "pull"})
    assert summary["pulled"] == 1 and summary["errors"] == []
    assert read(p)["parameter"]["Diameter"] == "5.00 mm"  # reverted
    assert plan_by_key(sync.plan_sync(str(tools_dir), server))[
        "bit:drill_5.0mm.fctb"]["action"] == "unchanged"


@pytest.mark.unit
def test_library_detail_shows_membership_delta(tools_dir):
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    sync.apply_sync(str(tools_dir), server, plan,
                    {i["key"]: "push" for i in plan["items"]})
    library = list(server.libraries.values())[0]
    probe_rid = read(tools_dir / "Bit" / "probe.fctb")["smooth"]["record_id"]
    library["tool_record_ids"] = library["tool_record_ids"] + [probe_rid]
    library["version"] += 1

    item = plan_by_key(sync.plan_sync(str(tools_dir), server))["lib:default.fctl"]
    assert item["action"] == "pull"
    assert "server adds: probe.fctb" in item["detail"]


@pytest.mark.unit
def test_editor_reformat_is_not_a_change(tools_dir):
    """Field finding: FreeCAD's ToolBit editor rewrites quantity formatting
    on every save ('6.0000 mm' -> '6.00 mm'). Semantically identical files
    must classify 'unchanged' and produce no diff noise; a single real edit
    must yield exactly one diff line."""
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    sync.apply_sync(str(tools_dir), server, plan,
                    {i["key"]: "push" for i in plan["items"]})

    # simulate the editor: reformat every quantity, drop the smooth key,
    # but change nothing semantically
    p = tools_dir / "Bit" / "end_mill_6.0mm_2f.fctb"
    doc = read(p)
    smooth_key = doc.pop("smooth")
    for k, v in list(doc["parameter"].items()):
        if isinstance(v, str) and v.endswith(" mm"):
            doc["parameter"][k] = "%.4f mm" % float(v.split()[0])
    doc["smooth"] = smooth_key
    p.write_text(json.dumps(doc))

    item = plan_by_key(sync.plan_sync(str(tools_dir), server))["bit:end_mill_6.0mm_2f.fctb"]
    assert item["action"] == "unchanged", item["diff"]

    # now ONE real edit on top of the reformat
    doc["name"] = "renamed"
    p.write_text(json.dumps(doc))
    item = plan_by_key(sync.plan_sync(str(tools_dir), server))["bit:end_mill_6.0mm_2f.fctb"]
    assert item["action"] == "push"
    assert len(item["diff"]) == 1
    assert item["diff"][0]["field"] == "name"
