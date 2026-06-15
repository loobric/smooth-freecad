# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the plan/apply sync over the **sectioned** schema (smooth-freecad#7).

The contract under test, exercised against the in-memory sectioned
``FakeServer`` (conftest), which returns ``{internal, canonical, clients}``
records exactly as smooth-core's tool-schema endpoints do:

- ``plan_sync`` classifies every item correctly and touches NOTHING (neither
  disk nor server).
- ``apply_sync`` executes only the selected decisions; everything else is left
  untouched on both sides.
- The decision is the human-chosen DIRECTION; the classification is only the
  suggested default — a 'push' uploads the local version (section write +
  canonical asserts), a 'pull' rewrites the file from the record.
- Identity survives the FreeCAD editors wiping the additive ``smooth`` key:
  re-adoption never duplicates a record, never matches by name.
- The endmill bug stays dead: a download lets the user choose/correct the tool
  type before the .fctb is synthesized, and a correction heals the server too.

This file replaces the v1 export_tools/import_tools suites; the plan/apply
model subsumes both directions.
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


def rid_of(record):
    return record["internal"]["id"]


def push_everything(tools_dir, server):
    """Plan, then apply a 'push' for every actionable item — the common
    'export the whole local dir' setup for the more interesting follow-ups."""
    plan = sync.plan_sync(str(tools_dir), server)
    decisions = {i["key"]: "push" for i in plan["items"]}
    return sync.apply_sync(str(tools_dir), server, plan, decisions)


def find_instance(server, client_item_id):
    """A pushed instance, located by the envelope handle FreeCAD sent."""
    for record in server.instances.values():
        section = record["clients"].get("freecad") or {}
        if section.get("client_item_id") == client_item_id:
            return record
    raise KeyError(client_item_id)


def server_rename(server, record, new_name):
    """A server-side canonical edit (the 'changed on the server' side)."""
    server.assert_instance(rid_of(record), "name", new_name)


def seed_server_instance(server, name, *, shape=None, diameter=None,
                         client_item_id=None, fctb=None):
    """A record that exists only on the server (born elsewhere, e.g. a
    machine-observed tool or another client's upload). ``fctb`` seeds a
    FreeCAD client section (the wrongly-stamped-endmill case); omit it for a
    tool with no FreeCAD representation at all."""
    data = {"fctb": fctb} if fctb is not None else None
    record = server.create_instance(data=data, client_item_id=client_item_id)
    record_id = rid_of(record)
    if name is not None:
        server.assert_instance(record_id, "name", name)
    if shape is not None:
        server.assert_instance(record_id, "geometry.shape", shape)
    if diameter is not None:
        server.assert_instance(record_id, "geometry.diameter", diameter)
    return server.instances[record_id]


def seed_server_set(server, name, member_ids, numbers=None):
    """A ToolSet that exists only on the server, with canonical membership."""
    record = server.create_set(data={"fctl_label": name, "version": 1})
    set_id = rid_of(record)
    server.assert_set(set_id, "name", name)
    members = [{"tool_record_id": record_id,
                "number": (numbers[i] if numbers else i + 1)}
               for i, record_id in enumerate(member_ids)]
    server.set_members(set_id, members)
    return server.sets[set_id]


def set_member_ids(server, set_id):
    return [m["tool_record_id"]
            for m in server.sets[set_id]["canonical"].get("members", [])]


# -- Plan is pure; fresh state classifies as new_local -----------------------

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
    # nothing was touched: files identical, server saw no writes
    assert {p.name: p.read_text() for p in (tools_dir / "Bit").glob("*")} == before
    assert server.instances == {} and server.sets == {}
    # membership info present for the dialog's grouping
    assert plan_by_key(plan)["bit:drill_5.0mm.fctb"]["library"] == "default.fctl"


@pytest.mark.unit
def test_apply_only_selected(tools_dir):
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {"bit:drill_5.0mm.fctb": "push"})
    assert summary["pushed"] == 1 and summary["errors"] == []
    assert len(server.instances) == 1
    assert "smooth" not in read(tools_dir / "Bit" / "probe.fctb")  # untouched


@pytest.mark.unit
def test_full_cycle_then_unchanged(tools_dir):
    server = FakeServer()
    summary = push_everything(tools_dir, server)
    assert summary["errors"] == []
    assert summary["pushed"] == 4          # 3 bits + 1 library

    plan = sync.plan_sync(str(tools_dir), server)
    assert {i["action"] for i in plan["items"]} == {"unchanged"}
    # the server holds exactly one object per file — no duplicates
    assert len(server.instances) == 3 and len(server.sets) == 1


# -- Classification of the four interesting directions -----------------------

@pytest.mark.unit
def test_plan_classifies_push_pull_conflict_and_new_server(tools_dir):
    server = FakeServer()
    push_everything(tools_dir, server)

    # local edit -> push
    p = tools_dir / "Bit" / "drill_5.0mm.fctb"
    doc = read(p); doc["parameter"]["Diameter"] = "5.10 mm"
    p.write_text(json.dumps(doc))
    # server edit -> pull
    server_rename(server, find_instance(server, "end_mill_6.0mm_2f"),
                  "server renamed")
    # both -> conflict
    pp = tools_dir / "Bit" / "probe.fctb"
    pdoc = read(pp); pdoc["parameter"]["Length"] = "55.0000 mm"
    pp.write_text(json.dumps(pdoc))
    server_rename(server, find_instance(server, "probe.fctb"), "server probe")
    # a record born on the server
    seed_server_instance(server, "born on server", shape="endmill",
                         diameter=8.0)

    plan = plan_by_key(sync.plan_sync(str(tools_dir), server))
    assert plan["bit:drill_5.0mm.fctb"]["action"] == "push"
    assert plan["bit:end_mill_6.0mm_2f.fctb"]["action"] == "pull"
    assert plan["bit:probe.fctb"]["action"] == "conflict"
    server_only = [i for i in plan.values() if i["action"] == "new_server"]
    assert len(server_only) == 1 and server_only[0]["name"] == "born on server"


@pytest.mark.unit
def test_conflict_keep_local_force_uploads(tools_dir):
    server = FakeServer()
    push_everything(tools_dir, server)
    p = tools_dir / "Bit" / "probe.fctb"
    doc = read(p); doc["parameter"]["Length"] = "55.0000 mm"
    p.write_text(json.dumps(doc))
    server_rename(server, find_instance(server, "probe.fctb"), "server probe")

    plan = sync.plan_sync(str(tools_dir), server)
    assert plan_by_key(plan)["bit:probe.fctb"]["action"] == "conflict"
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {"bit:probe.fctb": "push"})
    assert summary["errors"] == [] and summary["pushed"] == 1
    # local wins wholesale: the file's edit reached the server's .fctb section
    probe = find_instance(server, "probe.fctb")
    assert probe["clients"]["freecad"]["data"]["fctb"]["parameter"]["Length"] \
        == "55.0000 mm"
    plan = plan_by_key(sync.plan_sync(str(tools_dir), server))
    assert plan["bit:probe.fctb"]["action"] == "unchanged"


@pytest.mark.unit
def test_conflict_take_server_rewrites_file(tools_dir):
    server = FakeServer()
    push_everything(tools_dir, server)
    p = tools_dir / "Bit" / "probe.fctb"
    doc = read(p); doc["parameter"]["Length"] = "55.0000 mm"
    p.write_text(json.dumps(doc))
    server_rename(server, find_instance(server, "probe.fctb"), "server probe")

    plan = sync.plan_sync(str(tools_dir), server)
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {"bit:probe.fctb": "pull"})
    assert summary["pulled"] == 1
    after = read(p)
    assert after["name"] == "server probe"
    assert after["parameter"]["Length"] != "55.0000 mm"   # local edit discarded
    plan = plan_by_key(sync.plan_sync(str(tools_dir), server))
    assert plan["bit:probe.fctb"]["action"] == "unchanged"


@pytest.mark.unit
def test_diff_attributes_changes_to_the_right_side(tools_dir):
    server = FakeServer()
    push_everything(tools_dir, server)

    p = tools_dir / "Bit" / "drill_5.0mm.fctb"
    doc = read(p); doc["parameter"]["Diameter"] = "5.10 mm"
    p.write_text(json.dumps(doc))
    server_rename(server, find_instance(server, "drill_5.0mm"), "server rename")

    item = plan_by_key(sync.plan_sync(str(tools_dir), server))["bit:drill_5.0mm.fctb"]
    assert item["action"] == "conflict"
    by_field = {d["field"]: d for d in item["diff"]}
    assert by_field["parameter.Diameter"]["changed_by"] == "local"
    assert by_field["parameter.Diameter"]["local"] == "5.10 mm"
    assert by_field["name"]["changed_by"] == "server"
    assert by_field["name"]["server"] == "server rename"


@pytest.mark.unit
def test_direction_override_reverts_local_edit(tools_dir):
    """A locally-changed tool can be PULLED to discard the local edit — the
    user chooses direction; the classification is only a default."""
    server = FakeServer()
    push_everything(tools_dir, server)
    p = tools_dir / "Bit" / "drill_5.0mm.fctb"
    doc = read(p); doc["parameter"]["Diameter"] = "5.10 mm"
    p.write_text(json.dumps(doc))

    plan = sync.plan_sync(str(tools_dir), server)
    assert plan_by_key(plan)["bit:drill_5.0mm.fctb"]["action"] == "push"
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {"bit:drill_5.0mm.fctb": "pull"})
    assert summary["pulled"] == 1 and summary["errors"] == []
    assert read(p)["parameter"]["Diameter"] == "5.00 mm"   # reverted
    assert plan_by_key(sync.plan_sync(str(tools_dir), server))[
        "bit:drill_5.0mm.fctb"]["action"] == "unchanged"


@pytest.mark.unit
def test_editor_reformat_is_not_a_change(tools_dir):
    """FreeCAD's ToolBit editor rewrites quantity formatting on every save
    ('6.0000 mm' -> '6.00 mm'). Semantically identical files classify
    'unchanged' with no diff noise; one real edit yields exactly one diff."""
    server = FakeServer()
    push_everything(tools_dir, server)

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

    doc["name"] = "renamed"          # one real edit on top of the reformat
    p.write_text(json.dumps(doc))
    item = plan_by_key(sync.plan_sync(str(tools_dir), server))["bit:end_mill_6.0mm_2f.fctb"]
    assert item["action"] == "push"
    assert len(item["diff"]) == 1 and item["diff"][0]["field"] == "name"


# -- Deletions never silently resurrect or clobber ---------------------------

@pytest.mark.unit
def test_deleted_local_file_does_not_resurrect(tools_dir):
    """THE deletion bug: a locally-deleted tool classifies 'deleted_local'
    (not 'new on server'), defaults to skip, and propagates on explicit push."""
    server = FakeServer()
    push_everything(tools_dir, server)

    (tools_dir / "Bit" / "probe.fctb").unlink()
    item_map = plan_by_key(sync.plan_sync(str(tools_dir), server))
    deleted = [i for i in item_map.values() if i["action"] == "deleted_local"]
    assert len(deleted) == 1 and deleted[0]["name"] == "Probe"
    assert not [i for i in item_map.values() if i["action"] == "new_server"]

    plan = sync.plan_sync(str(tools_dir), server)
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {deleted[0]["key"]: "push"})
    assert summary["deleted"] == 1 and summary["errors"] == []
    assert len(server.instances) == 2
    # converged: nothing resurrects
    actions = {i["action"] for i in sync.plan_sync(str(tools_dir), server)["items"]}
    assert actions <= {"unchanged", "push"}   # library may show member removal


@pytest.mark.unit
def test_deleted_local_can_restore_instead(tools_dir):
    server = FakeServer()
    push_everything(tools_dir, server)
    (tools_dir / "Bit" / "probe.fctb").unlink()

    plan = sync.plan_sync(str(tools_dir), server)
    deleted = [i for i in plan["items"] if i["action"] == "deleted_local"][0]
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {deleted["key"]: "pull"})
    assert summary["pulled"] == 1
    assert (tools_dir / "Bit" / "probe.fctb").exists()


@pytest.mark.unit
def test_deleted_on_server_can_delete_local(tools_dir):
    server = FakeServer()
    push_everything(tools_dir, server)
    probe_rid = read(tools_dir / "Bit" / "probe.fctb")["smooth"]["record_id"]
    del server.instances[probe_rid]

    item = plan_by_key(sync.plan_sync(str(tools_dir), server))["bit:probe.fctb"]
    assert item["action"] == "deleted_server"

    plan = sync.plan_sync(str(tools_dir), server)
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {"bit:probe.fctb": "pull"})
    assert summary["deleted"] == 1
    assert not (tools_dir / "Bit" / "probe.fctb").exists()


@pytest.mark.unit
def test_deleted_server_record_recreated_on_push(tools_dir):
    """A stale id in the file (record deleted server-side) re-uploads as a
    fresh create on explicit push, never erroring forever."""
    server = FakeServer()
    push_everything(tools_dir, server)
    victim = read(tools_dir / "Bit" / "probe.fctb")["smooth"]["record_id"]
    del server.instances[victim]

    plan = sync.plan_sync(str(tools_dir), server)
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {"bit:probe.fctb": "push"})
    assert summary["errors"] == [] and summary["pushed"] == 1
    new_id = read(tools_dir / "Bit" / "probe.fctb")["smooth"]["record_id"]
    assert new_id != victim and new_id in server.instances


# -- Per-item errors surface, never silently dropped -------------------------

@pytest.mark.unit
def test_unreadable_bit_is_reported_and_rest_proceed(tools_dir):
    (tools_dir / "Bit" / "broken.fctb").write_text("{not json")
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    assert any("broken.fctb" in e for e in plan["errors"])
    # the readable bits still planned fine
    keys = {i["key"] for i in plan["items"]}
    assert "bit:drill_5.0mm.fctb" in keys


@pytest.mark.unit
def test_library_member_without_record_is_reported(tools_dir):
    """A library member whose .fctb was never uploaded is reported on push,
    not silently dropped; the resolvable members still go up."""
    fctl_path = tools_dir / "Library" / "default.fctl"
    doc = read(fctl_path)
    doc["tools"].append({"nr": 9, "path": "ghost.fctb"})
    fctl_path.write_text(json.dumps(doc))

    server = FakeServer()
    summary = push_everything(tools_dir, server)
    assert any("ghost.fctb" in e for e in summary["errors"])
    set_id = list(server.sets)[0]
    assert len(set_member_ids(server, set_id)) == 2   # the two real members


# -- Re-adoption after the editors wipe the identity key ---------------------

@pytest.mark.unit
def test_editor_wiped_smooth_key_bit_readopts_no_duplicate(tools_dir):
    """FreeCAD's ToolBit editor drops unknown top-level keys on save,
    destroying the 'smooth' identity key. Re-sync must re-adopt the server
    record by the .fctb 'id' (held server-side as client_item_id), never
    duplicate."""
    server = FakeServer()
    push_everything(tools_dir, server)
    assert len(server.instances) == 3

    path = tools_dir / "Bit" / "drill_5.0mm.fctb"
    doc = read(path)
    old_id = doc.pop("smooth")["record_id"]
    doc["parameter"]["Diameter"] = "5.20 mm"     # the edit the user made
    path.write_text(json.dumps(doc))

    item = plan_by_key(sync.plan_sync(str(tools_dir), server))["bit:drill_5.0mm.fctb"]
    assert item["action"] == "push"              # matched, not new_local
    assert rid_of(item["record"]) == old_id

    summary = sync.apply_sync(str(tools_dir), server,
                              sync.plan_sync(str(tools_dir), server),
                              {"bit:drill_5.0mm.fctb": "push"})
    assert summary["errors"] == []
    assert len(server.instances) == 3            # NO duplicate
    assert read(path)["smooth"]["record_id"] == old_id   # identity restored


@pytest.mark.unit
def test_editor_wiped_fctl_key_readopts_no_duplicate_library(tools_dir):
    """The library editor drops the smooth key the same way; re-adoption via
    the journal / recorded client_item_id updates the existing set, not a
    duplicate."""
    server = FakeServer()
    push_everything(tools_dir, server)
    assert len(server.sets) == 1

    fctl_path = tools_dir / "Library" / "default.fctl"
    doc = read(fctl_path)
    doc.pop("smooth")
    doc["tools"] = doc["tools"][:1]              # also an edit
    fctl_path.write_text(json.dumps(doc))

    item = plan_by_key(sync.plan_sync(str(tools_dir), server))["lib:default.fctl"]
    assert item["action"] == "push"              # matched, not new_local
    summary = sync.apply_sync(str(tools_dir), server,
                              sync.plan_sync(str(tools_dir), server),
                              {"lib:default.fctl": "push"})
    assert summary["errors"] == []
    assert len(server.sets) == 1                 # NO duplicate
    set_id = list(server.sets)[0]
    assert len(set_member_ids(server, set_id)) == 1
    assert "smooth" in read(fctl_path)           # identity restored


# -- Library membership deltas -----------------------------------------------

@pytest.mark.unit
def test_library_detail_shows_membership_delta(tools_dir):
    server = FakeServer()
    push_everything(tools_dir, server)
    set_id = list(server.sets)[0]
    probe_rid = read(tools_dir / "Bit" / "probe.fctb")["smooth"]["record_id"]
    # add probe to the server set's canonical membership
    members = [{"tool_record_id": rid} for rid in set_member_ids(server, set_id)]
    members.append({"tool_record_id": probe_rid})
    server.set_members(set_id, members)

    item = plan_by_key(sync.plan_sync(str(tools_dir), server))["lib:default.fctl"]
    assert item["action"] == "pull"
    assert "members only on server: probe.fctb" in item["detail"]


@pytest.mark.unit
def test_local_member_removal_pushes(tools_dir):
    """Removing a tool from a library locally pushes the removal to the
    canonical membership; wording must not imply the server ADDED it."""
    server = FakeServer()
    push_everything(tools_dir, server)

    fctl_path = tools_dir / "Library" / "default.fctl"
    doc = read(fctl_path)
    doc["tools"] = [t for t in doc["tools"] if t["path"] != "drill_5.0mm.fctb"]
    fctl_path.write_text(json.dumps(doc))

    item = plan_by_key(sync.plan_sync(str(tools_dir), server))["lib:default.fctl"]
    assert item["action"] == "push"
    assert "members only on server: drill_5.0mm.fctb" in item["detail"]

    sync.apply_sync(str(tools_dir), server, {"items": [item], "errors": []},
                    {"lib:default.fctl": "push"})
    set_id = list(server.sets)[0]
    assert len(set_member_ids(server, set_id)) == 1   # removal reached the server


# -- The tool-type picker / the endmill bug stays dead -----------------------

@pytest.mark.unit
def test_import_synthesizes_chosen_tool_type(tools_dir):
    """A server-only tool with no FreeCAD shape offers a type choice, and the
    download synthesizes the .fctb with the chosen shape (not always endmill)."""
    server = FakeServer()
    record = seed_server_instance(server, "Mystery 6mm", diameter=6.0)
    plan = sync.plan_sync(str(tools_dir), server)
    item = plan_by_key(plan)["server:%s" % rid_of(record)]
    assert item["action"] == "new_server"
    assert sync.needs_shape_choice(item) is True

    sync.apply_sync(str(tools_dir), server, plan,
                    {item["key"]: "pull"}, shapes={item["key"]: "drill"})
    docs = [read(p) for p in (tools_dir / "Bit").glob("*.fctb")]
    mystery = [d for d in docs if d.get("name") == "Mystery 6mm"]
    assert mystery, "expected a synthesized .fctb"
    assert mystery[0]["shape"] == "drill.fcstd"
    assert mystery[0]["shape-type"] == "Drill"


@pytest.mark.unit
def test_import_defaults_to_endmill_without_a_choice(tools_dir):
    """No shape chosen and no name hint -> the historical endmill default, so
    callers that pass no shapes are unsurprised."""
    server = FakeServer()
    record = seed_server_instance(server, "Plain 3mm", diameter=3.0)
    plan = sync.plan_sync(str(tools_dir), server)
    sync.apply_sync(str(tools_dir), server, plan,
                    {"server:%s" % rid_of(record): "pull"})
    docs = [read(p) for p in (tools_dir / "Bit").glob("*.fctb")]
    plain = [d for d in docs if d.get("name") == "Plain 3mm"][0]
    assert plain["shape"] == "endmill.fcstd"


@pytest.mark.unit
def test_import_corrects_a_wrongly_stamped_server_shape(tools_dir):
    """The pollution case: a record stored on the server as an endmill stub
    still offers a type choice on import, and choosing the right type rebuilds
    the .fctb — endmill is not forced."""
    server = FakeServer()
    record = seed_server_instance(
        server, "Probe", shape="endmill", diameter=3.0,
        fctb={"version": 2, "name": "Probe", "shape": "endmill.fcstd",
              "shape-type": "Endmill", "attribute": {},
              "parameter": {"Diameter": "3.00 mm"}})
    plan = sync.plan_sync(str(tools_dir), server)
    item = plan_by_key(plan)["server:%s" % rid_of(record)]
    assert item["action"] == "new_server"
    assert sync.needs_shape_choice(item) is True       # offered DESPITE the stamp

    sync.apply_sync(str(tools_dir), server, plan,
                    {item["key"]: "pull"}, shapes={item["key"]: "probe"})
    docs = [read(p) for p in (tools_dir / "Bit").glob("*.fctb")]
    probe = [d for d in docs if d.get("name") == "Probe"][0]
    assert probe["shape"] == "probe.fcstd"             # corrected, not endmill
    assert probe["shape-type"] == "Probe"


@pytest.mark.unit
def test_in_sync_bit_offers_no_type_choice(tools_dir):
    """A bit with nothing to download offers no type choice — the picker is
    for downloads only."""
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    decisions = {i["key"]: "push" for i in plan["items"] if i["kind"] == "bit"}
    sync.apply_sync(str(tools_dir), server, plan, decisions)
    for i in sync.plan_sync(str(tools_dir), server)["items"]:
        if i["kind"] == "bit":
            assert i["action"] == "unchanged"
            assert sync.needs_shape_choice(i) is False


@pytest.mark.unit
def test_server_born_record_materializes_as_new_file(tools_dir):
    server = FakeServer()
    push_everything(tools_dir, server)
    record = seed_server_instance(server, '1/4" downcut', shape="endmill",
                                  diameter=6.35)

    plan = sync.plan_sync(str(tools_dir), server)
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {"server:%s" % rid_of(record): "pull"})
    assert summary["pulled"] == 1
    new_files = [p for p in (tools_dir / "Bit").glob("*.fctb")
                 if "downcut" in p.name]
    assert len(new_files) == 1
    doc = read(new_files[0])
    assert doc["parameter"]["Diameter"] == "6.35 mm"
    assert "smooth" in doc                  # adopted: next sync updates, not creates


@pytest.mark.unit
def test_download_server_library_groups_and_does_not_recreate_records(tmp_path):
    """A server tool set + member records, nothing local:
    (1) each member bit groups UNDER the server library, never loose/duplicated;
    (2) downloading everything must NOT create new server records — the pulled
        files stay LINKED to the existing records (so they remain machine-bound).
    """
    (tmp_path / "Bit").mkdir()
    (tmp_path / "Library").mkdir()
    server = FakeServer()
    ids = []
    for n in (1, 2, 3):
        record = seed_server_instance(
            server, "T%d" % n, shape="endmill", diameter=float(n),
            fctb={"version": 2, "name": "T%d" % n, "shape": "endmill.fcstd",
                  "shape-type": "Endmill", "attribute": {},
                  "parameter": {"Diameter": "%.2f mm" % n}})
        ids.append(rid_of(record))
    tool_set = seed_server_set(server, "Set A", ids)

    plan = sync.plan_sync(str(tmp_path), server)
    by = plan_by_key(plan)
    lib = by["server-lib:%s" % rid_of(tool_set)]
    for record_id in ids:
        bit = by["server:%s" % record_id]
        assert bit["action"] == "new_server"
        assert bit["group"] == lib["group"]          # grouped under the library

    before_instances, before_sets = set(server.instances), set(server.sets)
    decisions = {i["key"]: "pull" for i in plan["items"]}
    shapes = {"server:%s" % ids[0]: "drill", "server:%s" % ids[1]: "probe"}
    summary = sync.apply_sync(str(tmp_path), server, plan, decisions, shapes=shapes)

    assert set(server.instances) == before_instances, "pull must NOT create records"
    assert set(server.sets) == before_sets, "pull must NOT create tool sets"
    assert summary["pushed"] == 0 and summary["pulled"] >= 3
    docs = {read(p).get("name"): read(p) for p in (tmp_path / "Bit").glob("*.fctb")}
    assert docs["T1"]["smooth"]["record_id"] == ids[0]    # stays linked
    assert docs["T1"]["shape"] == "drill.fcstd"            # chosen type applied
    assert docs["T2"]["shape"] == "probe.fcstd"


@pytest.mark.unit
def test_type_correction_on_download_heals_the_server_record(tmp_path):
    """Correcting a wrongly-stamped tool's type on download also fixes the
    SERVER record in place (keeping its id/binding) — so the server stops
    claiming endmill and the next sync converges."""
    (tmp_path / "Bit").mkdir()
    (tmp_path / "Library").mkdir()
    server = FakeServer()
    record = seed_server_instance(
        server, "Probe", shape="endmill", diameter=3.0,
        fctb={"version": 2, "name": "Probe", "shape": "endmill.fcstd",
              "shape-type": "Endmill", "attribute": {},
              "parameter": {"Diameter": "3.00 mm"}})
    record_id = rid_of(record)

    plan = sync.plan_sync(str(tmp_path), server)
    sync.apply_sync(str(tmp_path), server, plan,
                    {"server:%s" % record_id: "pull"},
                    shapes={"server:%s" % record_id: "probe"})

    assert record_id in server.instances                  # UPDATED, not recreated
    healed = server.instances[record_id]
    assert sync.record_shape(healed) == "probe"           # canonical shape fixed
    assert healed["clients"]["freecad"]["data"]["fctb"]["shape"] == "probe.fcstd"

    # the next sync is a no-op, not a pending push-back
    bit = next(i for i in sync.plan_sync(str(tmp_path), server)["items"]
               if i["kind"] == "bit"
               and (i.get("record") or {}).get("internal", {}).get("id") == record_id)
    assert bit["action"] == "unchanged"


@pytest.mark.unit
def test_plain_download_without_correction_does_not_touch_server(tmp_path):
    """A download with NO type change stays a pure pull — no server writes."""
    (tmp_path / "Bit").mkdir()
    (tmp_path / "Library").mkdir()
    server = FakeServer()
    record = seed_server_instance(
        server, "6mm EM", shape="endmill", diameter=6.0,
        fctb={"version": 2, "name": "6mm EM", "shape": "endmill.fcstd",
              "shape-type": "Endmill", "attribute": {},
              "parameter": {"Diameter": "6.00 mm"}})
    record_id = rid_of(record)
    snapshot = json.dumps(server.instances[record_id], sort_keys=True)

    plan = sync.plan_sync(str(tmp_path), server)
    sync.apply_sync(str(tmp_path), server, plan,
                    {"server:%s" % record_id: "pull"},
                    shapes={"server:%s" % record_id: "endmill"})
    assert json.dumps(server.instances[record_id], sort_keys=True) == snapshot
