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
    assert server.records == {} and server.tool_sets == {}
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
    library = list(server.tool_sets.values())[0]
    probe_rid = read(tools_dir / "Bit" / "probe.fctb")["smooth"]["record_id"]
    library["tool_record_ids"] = library["tool_record_ids"] + [probe_rid]
    library["version"] += 1

    item = plan_by_key(sync.plan_sync(str(tools_dir), server))["lib:default.fctl"]
    assert item["action"] == "pull"
    assert "members only on server: probe.fctb" in item["detail"]


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


@pytest.mark.unit
def test_local_member_removal_pushes_and_reports_neutrally(tools_dir):
    """Removing a tool from a library locally: the delta says the member is
    'only here'... wait - removed locally means only on SERVER. Wording must
    not imply the server ADDED it."""
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    sync.apply_sync(str(tools_dir), server, plan,
                    {i["key"]: "push" for i in plan["items"]})

    fctl_path = tools_dir / "Library" / "default.fctl"
    doc = read(fctl_path)
    doc["tools"] = [t for t in doc["tools"] if t["path"] != "drill_5.0mm.fctb"]
    fctl_path.write_text(json.dumps(doc))

    item = plan_by_key(sync.plan_sync(str(tools_dir), server))["lib:default.fctl"]
    assert item["action"] == "push"
    assert "members only on server: drill_5.0mm.fctb" in item["detail"]

    sync.apply_sync(str(tools_dir), server, {"items": [item], "errors": []},
                    {"lib:default.fctl": "push"})
    library = list(server.tool_sets.values())[0]
    assert len(library["tool_record_ids"]) == 1  # removal reached the server


@pytest.mark.unit
def test_deleted_local_file_does_not_resurrect(tools_dir):
    """THE deletion bug: a locally-deleted tool must classify 'deleted_local'
    (not 'new on server'), default to skip, and propagate on explicit push."""
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    sync.apply_sync(str(tools_dir), server, plan,
                    {i["key"]: "push" for i in plan["items"]})

    (tools_dir / "Bit" / "probe.fctb").unlink()
    item_map = plan_by_key(sync.plan_sync(str(tools_dir), server))
    deleted = [i for i in item_map.values() if i["action"] == "deleted_local"]
    assert len(deleted) == 1
    assert deleted[0]["name"] == "Probe"
    assert not [i for i in item_map.values() if i["action"] == "new_server"]

    # explicit choice: propagate the deletion
    plan = sync.plan_sync(str(tools_dir), server)
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {deleted[0]["key"]: "push"})
    assert summary["deleted"] == 1 and summary["errors"] == []
    assert len(server.records) == 2
    # converged: no deletion row remains, nothing resurrects
    actions = {i["action"] for i in sync.plan_sync(str(tools_dir), server)["items"]}
    assert actions <= {"unchanged", "push"}  # library may show member removal


@pytest.mark.unit
def test_deleted_local_can_restore_instead(tools_dir):
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    sync.apply_sync(str(tools_dir), server, plan,
                    {i["key"]: "push" for i in plan["items"]})
    (tools_dir / "Bit" / "probe.fctb").unlink()

    plan = sync.plan_sync(str(tools_dir), server)
    deleted = [i for i in plan["items"] if i["action"] == "deleted_local"][0]
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {deleted["key"]: "pull"})
    assert summary["pulled"] == 1
    assert (tools_dir / "Bit" / "probe.fctb").exists()


@pytest.mark.unit
def test_deleted_on_server_does_not_reupload_silently(tools_dir):
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    sync.apply_sync(str(tools_dir), server, plan,
                    {i["key"]: "push" for i in plan["items"]})
    probe_rid = read(tools_dir / "Bit" / "probe.fctb")["smooth"]["record_id"]
    del server.records[probe_rid]

    item_map = plan_by_key(sync.plan_sync(str(tools_dir), server))
    item = item_map["bit:probe.fctb"]
    assert item["action"] == "deleted_server"

    # choice A: delete the local file too
    plan = sync.plan_sync(str(tools_dir), server)
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {"bit:probe.fctb": "pull"})
    assert summary["deleted"] == 1
    assert not (tools_dir / "Bit" / "probe.fctb").exists()


@pytest.mark.unit
def test_editor_wiped_fctl_key_readopts_no_duplicate_library(tools_dir):
    """Field finding: FreeCAD's library editor drops the smooth key on
    save (same as the ToolBit editor) - the next sync created a DUPLICATE
    library. Re-adoption via journal/recorded filename must update the
    existing one instead."""
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    sync.apply_sync(str(tools_dir), server, plan,
                    {i["key"]: "push" for i in plan["items"]})
    assert len(server.tool_sets) == 1

    # simulate the library editor: rewrite without the smooth key + an edit
    fctl_path = tools_dir / "Library" / "default.fctl"
    doc = read(fctl_path)
    doc.pop("smooth")
    doc["tools"] = doc["tools"][:1]
    fctl_path.write_text(json.dumps(doc))

    item = plan_by_key(sync.plan_sync(str(tools_dir), server))["lib:default.fctl"]
    assert item["action"] == "push"          # matched, not new_local
    summary = sync.apply_sync(str(tools_dir), server,
                              sync.plan_sync(str(tools_dir), server),
                              {"lib:default.fctl": "push"})
    assert summary["errors"] == []
    assert len(server.tool_sets) == 1        # NO duplicate
    assert len(list(server.tool_sets.values())[0]["tool_record_ids"]) == 1
    assert "smooth" in read(fctl_path)       # identity restored


@pytest.mark.unit
def test_legacy_library_id_key_and_journal_still_match(tools_dir):
    """Back-compat for the 2026-06-11 nomenclature purge: files written
    before it spell the identity key 'library_id' and the journal bucket
    'libraries'. Both must keep matching - no duplicate tool set - and
    the next writeback upgrades the spelling."""
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    sync.apply_sync(str(tools_dir), server, plan,
                    {i["key"]: "push" for i in plan["items"]})
    assert len(server.tool_sets) == 1
    set_id = list(server.tool_sets)[0]

    # rewrite the .fctl identity in the legacy spelling
    fctl_path = tools_dir / "Library" / "default.fctl"
    doc = read(fctl_path)
    version = doc["smooth"]["version"]
    doc["smooth"] = {"library_id": set_id, "version": version}
    fctl_path.write_text(json.dumps(doc))
    # and the journal in the legacy bucket name
    state_path = tools_dir / ".smooth_state.json"
    state = json.loads(state_path.read_text())
    state["libraries"] = state.pop("tool_sets")
    state_path.write_text(json.dumps(state))

    item = plan_by_key(sync.plan_sync(str(tools_dir), server))["lib:default.fctl"]
    assert item["action"] == "unchanged"     # matched via legacy key

    # an edit pushes as an update (never a duplicate) and upgrades the key
    doc["tools"] = doc["tools"][:1]
    fctl_path.write_text(json.dumps(doc))
    sync.apply_sync(str(tools_dir), server,
                    sync.plan_sync(str(tools_dir), server),
                    {"lib:default.fctl": "push"})
    assert len(server.tool_sets) == 1        # NO duplicate
    assert read(fctl_path)["smooth"].get("tool_set_id") == set_id
    assert "tool_sets" in json.loads(state_path.read_text())


@pytest.mark.unit
def test_import_synthesizes_chosen_tool_type(tools_dir):
    """A server-only tool with no FreeCAD shape offers a type choice, and the
    download synthesizes the .fctb with the chosen shape (not always endmill)."""
    server = FakeServer()
    server.records["rec-x"] = {
        "id": "rec-x", "version": 1, "name": "Mystery 6mm",
        "geometry": {"diameter": 6.0, "diameter_unit": "mm"}, "extra": {},
    }
    plan = sync.plan_sync(str(tools_dir), server)
    item = plan_by_key(plan)["server:rec-x"]
    assert item["action"] == "new_server"
    assert sync.needs_shape_choice(item) is True

    sync.apply_sync(str(tools_dir), server, plan,
                    {"server:rec-x": "pull"}, shapes={"server:rec-x": "drill"})
    docs = [read(p) for p in (tools_dir / "Bit").glob("*.fctb")]
    mystery = [d for d in docs if d.get("name") == "Mystery 6mm"]
    assert mystery, "expected a synthesized .fctb"
    assert mystery[0]["shape"] == "drill.fcstd"
    assert mystery[0]["shape-type"] == "Drill"


@pytest.mark.unit
def test_import_defaults_to_endmill_without_a_choice(tools_dir):
    """No shape chosen -> historical default, so behavior is unchanged for
    callers that don't pass shapes."""
    server = FakeServer()
    server.records["rec-y"] = {
        "id": "rec-y", "version": 1, "name": "Plain 3mm",
        "geometry": {"diameter": 3.0}, "extra": {},
    }
    plan = sync.plan_sync(str(tools_dir), server)
    sync.apply_sync(str(tools_dir), server, plan, {"server:rec-y": "pull"})
    docs = [read(p) for p in (tools_dir / "Bit").glob("*.fctb")]
    plain = [d for d in docs if d.get("name") == "Plain 3mm"][0]
    assert plain["shape"] == "endmill.fcstd"


@pytest.mark.unit
def test_import_corrects_a_wrongly_stamped_server_shape(tools_dir):
    """The pollution case: a record stored on the server as an endmill stub
    (Probe etc.) still offers a type choice on import, and choosing the right
    type rebuilds the .fctb — endmill is not forced."""
    server = FakeServer()
    server.records["rec-p"] = {
        "id": "rec-p", "version": 1, "name": "Probe",
        "geometry": {"shape": "endmill", "diameter": 3.0},
        "extra": {"freecad": {"fctb": {
            "version": 2, "name": "Probe", "shape": "endmill.fcstd",
            "shape-type": "Endmill", "attribute": {},
            "parameter": {"Diameter": "3.00 mm"}}}},
    }
    plan = sync.plan_sync(str(tools_dir), server)
    item = plan_by_key(plan)["server:rec-p"]
    assert item["action"] == "new_server"
    assert sync.needs_shape_choice(item) is True          # offered DESPITE endmill stamp

    sync.apply_sync(str(tools_dir), server, plan,
                    {"server:rec-p": "pull"}, shapes={"server:rec-p": "probe"})
    docs = [read(p) for p in (tools_dir / "Bit").glob("*.fctb")]
    probe = [d for d in docs if d.get("name") == "Probe"][0]
    assert probe["shape"] == "probe.fcstd"                # corrected, not endmill
    assert probe["shape-type"] == "Probe"


@pytest.mark.unit
def test_in_sync_bit_offers_no_type_choice(tools_dir):
    """A bit with nothing to download (in sync / upload-only) offers no type
    choice — the picker is for downloads only."""
    server = FakeServer()
    plan = sync.plan_sync(str(tools_dir), server)
    decisions = {i["key"]: "push" for i in plan["items"] if i["kind"] == "bit"}
    sync.apply_sync(str(tools_dir), server, plan, decisions)
    plan2 = sync.plan_sync(str(tools_dir), server)
    for i in plan2["items"]:
        if i["kind"] == "bit":
            assert i["action"] == "unchanged"
            assert sync.needs_shape_choice(i) is False


@pytest.mark.unit
def test_download_server_library_groups_and_does_not_recreate_records(tmp_path):
    """Field repro: a server tool set + member records, nothing local.
    (1) each member bit groups UNDER the server library, never loose/duplicated;
    (2) downloading everything must NOT create new server records — the pulled
        files stay LINKED to the existing records (so they remain machine-bound).
    """
    (tmp_path / "Bit").mkdir()
    (tmp_path / "Library").mkdir()
    server = FakeServer()
    ids = []
    for n in (1, 2, 3):
        r = server.create_records([{
            "name": "T%d" % n,
            "geometry": {"shape": "endmill", "diameter": float(n)},
            "extra": {"freecad": {"fctb": {
                "version": 2, "name": "T%d" % n, "shape": "endmill.fcstd",
                "shape-type": "Endmill", "attribute": {},
                "parameter": {"Diameter": "%.2f mm" % n}}}},
        }])["items"][0]
        ids.append(r["id"])
    ts = server.create_tool_sets([{
        "name": "Set A", "tool_record_ids": ids, "extra": {"freecad": {}}}])["items"][0]

    plan = sync.plan_sync(str(tmp_path), server)
    by = plan_by_key(plan)
    lib = by["server-lib:%s" % ts["id"]]
    for rid in ids:
        bit = by["server:%s" % rid]
        assert bit["action"] == "new_server"
        assert bit["group"] == lib["group"]          # grouped under the library

    before_records, before_sets = set(server.records), set(server.tool_sets)
    decisions = {i["key"]: "pull" for i in plan["items"]}
    shapes = {"server:%s" % ids[0]: "drill", "server:%s" % ids[1]: "probe"}
    summary = sync.apply_sync(str(tmp_path), server, plan, decisions, shapes=shapes)

    assert set(server.records) == before_records, "pull must NOT create server records"
    assert set(server.tool_sets) == before_sets, "pull must NOT create tool sets"
    assert summary["pushed"] == 0
    assert summary["pulled"] >= 3
    docs = {read(p).get("name"): read(p) for p in (tmp_path / "Bit").glob("*.fctb")}
    assert docs["T1"]["smooth"]["record_id"] == ids[0]   # stays linked
    assert docs["T1"]["shape"] == "drill.fcstd"           # chosen type applied
    assert docs["T2"]["shape"] == "probe.fcstd"


@pytest.mark.unit
def test_type_correction_on_download_heals_the_server_record(tmp_path):
    """Correcting a wrongly-stamped tool's type on download must also fix the
    SERVER record (in place, keeping its id/binding) — not just the local file —
    so the server stops claiming endmill and the next sync converges."""
    (tmp_path / "Bit").mkdir()
    (tmp_path / "Library").mkdir()
    server = FakeServer()
    rec = server.create_records([{
        "name": "Probe", "geometry": {"shape": "endmill", "diameter": 3.0},
        "extra": {"freecad": {"fctb": {
            "version": 2, "name": "Probe", "shape": "endmill.fcstd",
            "shape-type": "Endmill", "attribute": {},
            "parameter": {"Diameter": "3.00 mm"}}}},
    }])["items"][0]
    rid = rec["id"]

    plan = sync.plan_sync(str(tmp_path), server)
    sync.apply_sync(str(tmp_path), server, plan,
                    {"server:%s" % rid: "pull"}, shapes={"server:%s" % rid: "probe"})

    assert rid in server.records                       # UPDATED in place, not recreated
    healed = server.records[rid]
    assert healed["geometry"]["shape"] == "probe"      # canonical shape fixed
    assert healed["extra"]["freecad"]["fctb"]["shape"] == "probe.fcstd"

    # cycle converges: the very next sync is a no-op, not a pending push-back
    plan2 = sync.plan_sync(str(tmp_path), server)
    bit = next(i for i in plan2["items"]
               if i["kind"] == "bit" and (i.get("record") or {}).get("id") == rid)
    assert bit["action"] == "unchanged"


@pytest.mark.unit
def test_plain_download_without_correction_does_not_touch_server(tmp_path):
    """A download with NO type change stays a pure pull — no server writes."""
    (tmp_path / "Bit").mkdir()
    (tmp_path / "Library").mkdir()
    server = FakeServer()
    rec = server.create_records([{
        "name": "6mm EM", "geometry": {"shape": "endmill", "diameter": 6.0},
        "extra": {"freecad": {"fctb": {
            "version": 2, "name": "6mm EM", "shape": "endmill.fcstd",
            "shape-type": "Endmill", "attribute": {}, "parameter": {"Diameter": "6.00 mm"}}}},
    }])["items"][0]
    snapshot = json.dumps(rec, sort_keys=True)
    plan = sync.plan_sync(str(tmp_path), server)
    # download, leaving the type as-is (endmill)
    sync.apply_sync(str(tmp_path), server, plan,
                    {"server:%s" % rec["id"]: "pull"}, shapes={"server:%s" % rec["id"]: "endmill"})
    assert json.dumps(server.records[rec["id"]], sort_keys=True) == snapshot  # untouched
