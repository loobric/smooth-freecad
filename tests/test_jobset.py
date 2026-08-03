# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the job→ToolSet flow (jobset.py — pure, headless) and its read-only
rendering in the sync plan.

The contract (grilled & locked 2026-07-30): NO .fctl is ever written — the set
is server-side only, stamped with job provenance; unresolvable and
double-numbered tools are refused (reported, never silent); two different
tools on one number block the command; re-runs replace members wholesale; a
job-origin set renders in the sync plan as read-only 'job_set', never as a
downloadable 'new_server' library.
"""
import copy
import json
import os

import pytest

from conftest import FakeServer
from freecad.Loobric import jobset, sync
from freecad.Loobric.jobset import JobSetError


def _doc(tools_dir, basename):
    with open(os.path.join(str(tools_dir), "Bit", basename)) as f:
        return json.load(f)


def _tc(name, number, bit_id, embedded=None):
    return {"name": name, "number": number, "bit_id": bit_id,
            "embedded": embedded}


# -- plan_job_set ------------------------------------------------------------

@pytest.mark.unit
def test_plan_resolves_dedupes_and_refuses_unresolved(tools_dir):
    controllers = [
        _tc("Drill 5.0mm", 1, "drill_5.0mm"),
        _tc("End Mill 6.0mm 2F", 2, "end_mill_6.0mm_2f"),
        # the same tool in a second controller (different feeds) — one member
        _tc("End Mill 6.0mm 2F", 2, "end_mill_6.0mm_2f"),
        # an ad-hoc toolbit with no asset file — refused, never silent
        _tc("Ad hoc chamfer", 5, "no_such_bit"),
    ]
    plan = jobset.plan_job_set(controllers, str(tools_dir), [])
    assert [(m["number"], m["basename"]) for m in plan["members"]] == [
        (1, "drill_5.0mm.fctb"), (2, "end_mill_6.0mm_2f.fctb")]
    assert all(m["needs_upload"] for m in plan["members"])   # empty server
    [excluded] = plan["excluded"]
    assert excluded["name"] == "Ad hoc chamfer"
    assert "save this tool to your tool library" in excluded["reason"]


@pytest.mark.unit
def test_plan_matches_by_filename_stem_not_in_file_id(tools_dir):
    """A ToolBitID is the ASSET id — the filename stem — because FreeCAD's
    asset deserializer overwrites any in-file "id" on load. probe.fctb ships
    with ``id: null``, and regenerated downloads may carry no id at all; both
    must still resolve by stem (the in-FreeCAD failure of 2026-07-31)."""
    assert _doc(tools_dir, "probe.fctb").get("id") is None
    plan = jobset.plan_job_set([_tc("Probe", 99, "probe")], str(tools_dir), [])
    assert plan["excluded"] == []
    [member] = plan["members"]
    assert member["basename"] == "probe.fctb" and member["number"] == 99


@pytest.mark.unit
def test_drift_check_ignores_identity_plumbing(tools_dir):
    """The embedded copy's "id" is stamped with the asset stem at load, so an
    id difference alone is not tool drift."""
    embedded = copy.deepcopy(_doc(tools_dir, "probe.fctb"))
    embedded["id"] = "probe"                      # what FreeCAD actually holds
    plan = jobset.plan_job_set([_tc("Probe", 9, "probe", embedded=embedded)],
                               str(tools_dir), [])
    assert plan["members"][0]["drifted"] is False


@pytest.mark.unit
def test_same_tool_under_two_numbers_is_refused(tools_dir):
    """The strict call from the grill: the programmer renumbers the job; the
    command never picks a winner."""
    controllers = [_tc("Drill 5.0mm", 3, "drill_5.0mm"),
                   _tc("Drill 5.0mm", 7, "drill_5.0mm")]
    plan = jobset.plan_job_set(controllers, str(tools_dir), [])
    assert plan["members"] == []
    [excluded] = plan["excluded"]
    assert "used as T3 and T7" in excluded["reason"]
    assert "renumber" in excluded["reason"]


@pytest.mark.unit
def test_two_tools_claiming_one_number_blocks_the_command(tools_dir):
    """A genuine job error — the machine cannot hold two tools at one number —
    blocks set creation entirely (the client-side interlock MAPPING_PLAN
    permits)."""
    controllers = [_tc("Drill 5.0mm", 3, "drill_5.0mm"),
                   _tc("End Mill 6.0mm 2F", 3, "end_mill_6.0mm_2f")]
    with pytest.raises(JobSetError) as err:
        jobset.plan_job_set(controllers, str(tools_dir), [])
    assert "T3" in str(err.value)
    assert "Drill 5.0mm" in str(err.value)
    assert "End Mill 6.0mm 2F" in str(err.value)


@pytest.mark.unit
def test_drift_between_job_copy_and_asset_file_is_flagged(tools_dir):
    clean = _doc(tools_dir, "drill_5.0mm.fctb")
    drifted = copy.deepcopy(clean)
    drifted["parameter"]["Diameter"] = "4.50 mm"     # edited inside the job
    plan = jobset.plan_job_set(
        [_tc("Drill 5.0mm", 1, "drill_5.0mm", embedded=clean),
         _tc("End Mill", 2, "end_mill_6.0mm_2f",
             embedded=None)],                        # no serialization: no flag
        str(tools_dir), [])
    assert [m["drifted"] for m in plan["members"]] == [False, False]

    plan = jobset.plan_job_set(
        [_tc("Drill 5.0mm", 1, "drill_5.0mm", embedded=drifted)],
        str(tools_dir), [])
    assert plan["members"][0]["drifted"] is True


@pytest.mark.unit
def test_plan_matches_existing_server_records(tools_dir):
    server = FakeServer()
    # the server already knows this bit (client_item_id = the .fctb id)
    rec = server.create_instance({"fctb": {}}, client_item_id="drill_5.0mm")
    plan = jobset.plan_job_set(
        [_tc("Drill 5.0mm", 1, "drill_5.0mm"),
         _tc("End Mill", 2, "end_mill_6.0mm_2f")],
        str(tools_dir), server.list_instances())
    drill, endmill = plan["members"]
    assert drill["tool_record_id"] == rec["internal"]["id"]
    assert drill["needs_upload"] is False
    assert endmill["tool_record_id"] is None and endmill["needs_upload"]


# -- apply_job_set -----------------------------------------------------------

def _plan(tools_dir, server, numbers=(1, 2)):
    return jobset.plan_job_set(
        [_tc("Drill 5.0mm", numbers[0], "drill_5.0mm"),
         _tc("End Mill 6.0mm 2F", numbers[1], "end_mill_6.0mm_2f")],
        str(tools_dir), server.list_instances())


@pytest.mark.unit
def test_apply_creates_set_with_claims_provenance_and_uploads(tools_dir):
    server = FakeServer()
    result = jobset.apply_job_set(
        server, str(tools_dir), _plan(tools_dir, server), "bracket — Job",
        {"document": "bracket", "job": "Job"})
    assert result["created"] is True and result["name_conflict"] is False

    record = server.get_set(result["set_id"])
    # provenance stamp — what the sync plan keys the read-only rendering on
    data = record["clients"]["freecad"]["data"]
    assert data == {"origin": "job", "document": "bracket", "job": "Job"}
    # the T-numbers became member claims
    members = record["canonical"]["members"]
    assert [m["number"]["value"] for m in members] == [1, 2]
    # missing bits were uploaded with full identity write-back
    assert len(server.instances) == 2
    for basename in ("drill_5.0mm.fctb", "end_mill_6.0mm_2f.fctb"):
        doc = _doc(tools_dir, basename)
        assert doc["loobric"]["record_id"] in server.instances
    # the name was claimed
    assert record["canonical"]["name"]["value"] == "bracket — Job"
    # and NO .fctl was written — the user's libraries are untouched
    assert sorted(os.listdir(os.path.join(str(tools_dir), "Library"))) == \
        ["default.fctl"]


@pytest.mark.unit
def test_apply_rerun_replaces_members_wholesale(tools_dir):
    server = FakeServer()
    first = jobset.apply_job_set(
        server, str(tools_dir), _plan(tools_dir, server), "bracket — Job",
        {"document": "bracket", "job": "Job"})
    sid = first["set_id"]
    # a foreign actor adds a member by hand; the job then renumbers + drops it
    server.set_members(sid, [
        {"tool_record_id": m["tool_record_id"]["value"]
         if isinstance(m["tool_record_id"], dict) else m["tool_record_id"],
         "number": m["number"]["value"]}
        for m in server.get_set(sid)["canonical"]["members"]] + [
        {"tool_record_id": "foreign-1", "number": 30}], actor="human@web")

    plan = _plan(tools_dir, server, numbers=(7, 2))
    result = jobset.apply_job_set(
        server, str(tools_dir), plan, "bracket — Job",
        {"document": "bracket", "job": "Job"}, set_id=sid)
    assert result["set_id"] == sid and result["created"] is False
    members = server.get_set(sid)["canonical"]["members"]
    assert [m["number"]["value"] for m in members] == [2, 7]   # wholesale,
    assert all(m["tool_record_id"] != "foreign-1" for m in members)  # by claim


@pytest.mark.unit
def test_apply_adopts_by_provenance_when_property_lost(tools_dir):
    server = FakeServer()
    first = jobset.apply_job_set(
        server, str(tools_dir), _plan(tools_dir, server), "bracket — Job",
        {"document": "bracket", "job": "Job"})
    again = jobset.apply_job_set(
        server, str(tools_dir), _plan(tools_dir, server), "bracket — Job",
        {"document": "bracket", "job": "Job"}, set_id=None)
    assert again["set_id"] == first["set_id"]      # no sibling set
    assert again["created"] is False
    assert len(server.sets) == 1


@pytest.mark.unit
def test_apply_name_collision_is_reported_not_silent(tools_dir):
    server = FakeServer()
    other = server.create_set()
    server.assert_set(other["internal"]["id"], "name", "bracket — Job",
                      actor="human@web")
    result = jobset.apply_job_set(
        server, str(tools_dir), _plan(tools_dir, server), "bracket — Job",
        {"document": "bracket", "job": "Job"})
    # the set is fully created (members, provenance) — only the name is held
    assert result["name_conflict"] is True
    record = server.get_set(result["set_id"])
    assert len(record["canonical"]["members"]) == 2
    assert "name" not in record["canonical"]
    # the re-prompt path claims a free name
    assert jobset.claim_set_name(server, result["set_id"], "bracket — Job 2")
    assert server.get_set(result["set_id"])["canonical"]["name"]["value"] \
        == "bracket — Job 2"
    # ...and still refuses the taken one
    assert not jobset.claim_set_name(server, result["set_id"], "bracket — Job")


# -- member_delta ------------------------------------------------------------

@pytest.mark.unit
def test_member_delta_names_adds_renumbers_and_drops():
    existing = {"canonical": {"members": [
        {"tool_record_id": "a", "number": {"value": 1}},
        {"tool_record_id": "b", "number": {"value": 2}},
    ]}}
    members = [
        {"tool_record_id": "a", "number": 7, "name": "drill"},     # renumber
        {"tool_record_id": "c", "number": 3, "name": "chamfer"},   # added
    ]                                                              # b dropped
    lines = jobset.member_delta(existing, members)
    assert "T1 → T7  drill" in lines
    assert "+ T3  chamfer" in lines
    assert "− T2  (no longer in the job)" in lines


# -- the sync plan renders a job-origin set read-only ------------------------

@pytest.mark.unit
def test_plan_sync_classifies_job_origin_set_as_read_only(tools_dir):
    server = FakeServer()
    jobset.apply_job_set(
        server, str(tools_dir), _plan(tools_dir, server), "bracket — Job",
        {"document": "bracket", "job": "Job"})

    plan = sync.plan_sync(str(tools_dir), server)
    [item] = [i for i in plan["items"] if i["kind"] == "library"
              and i.get("record") and i["record"]["clients"]
              .get("freecad", {}).get("data", {}).get("origin") == "job"]
    # read-only 'job_set' — NEVER the downloadable 'new_server' that would
    # materialize the .fctl this feature promises not to create
    assert item["action"] == "job_set"
    assert "from CAM job 'Job'" in item["detail"]
    assert "read-only" in item["detail"]

    # apply ignores it even if a decision sneaks in
    summary = sync.apply_sync(str(tools_dir), server, plan,
                              {item["key"]: "pull"})
    assert summary["pulled"] == 0 and summary["errors"] == []
    assert sorted(os.listdir(os.path.join(str(tools_dir), "Library"))) == \
        ["default.fctl"]
