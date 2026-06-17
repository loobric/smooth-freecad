# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the exceptions-first Needs-Attention logic (issue #9), all pure
``uihelpers`` functions (no FreeCAD/PySide) so they run headless.

They pin the contracts the GUI tabs lean on:
- the Existence × Sync status model is derived from a plan action and the two
  axes stay separate;
- the CAM-content axis drops in-sync objects and keeps one row per object;
- the machine-coverage axis reuses coverage_rows but keeps only the rows that
  need action (the absent_on_machine / mismatch / collision exceptions);
- the aggregated stream sorts worst-first;
- the per-library rollup summarizes CONTENTS without the library being itself
  'conflicted';
- the resolution view exposes a side-by-side field comparison with provenance
  and a stable choice -> apply-direction mapping.
"""
import pytest

from freecad.Smooth.uihelpers import (
    EXIST_LOCAL_ONLY, EXIST_SERVER_ONLY, EXIST_BOTH,
    SYNC_SYNCED, SYNC_MODIFIED, SYNC_CONFLICT, SYNC_PENDING, SYNC_ERROR,
    existence_of, sync_status_of, is_exception,
    content_status_info, content_attention_rows,
    coverage_attention_rows, needs_attention_rows,
    library_rollup, library_rollup_line,
    field_provenance, resolution_rows, resolution_actions, RESOLUTION_DECISION,
    SEV_CRITICAL, SEV_WARNING, SEV_INFO, SEV_OK)


# -- status model: existence and sync are separate axes ----------------------

@pytest.mark.unit
@pytest.mark.parametrize("action,existence,sync", [
    ("unchanged", EXIST_BOTH, SYNC_SYNCED),
    ("push", EXIST_BOTH, SYNC_MODIFIED),
    ("pull", EXIST_BOTH, SYNC_MODIFIED),
    ("conflict", EXIST_BOTH, SYNC_CONFLICT),
    ("new_local", EXIST_LOCAL_ONLY, SYNC_PENDING),
    ("new_server", EXIST_SERVER_ONLY, SYNC_PENDING),
    ("deleted_local", EXIST_SERVER_ONLY, SYNC_PENDING),
    ("deleted_server", EXIST_LOCAL_ONLY, SYNC_PENDING),
])
def test_existence_and_sync_bands(action, existence, sync):
    assert existence_of(action) == existence
    assert sync_status_of(action) == sync


@pytest.mark.unit
def test_unknown_action_degrades_without_raising():
    assert existence_of("future_action") == EXIST_BOTH
    assert sync_status_of("future_action") == SYNC_ERROR


@pytest.mark.unit
def test_is_exception_only_for_unsynced():
    assert is_exception("unchanged") is False
    for action in ("push", "pull", "conflict", "new_local", "new_server",
                   "deleted_local", "deleted_server"):
        assert is_exception(action) is True


# -- CAM-content axis --------------------------------------------------------

def _plan_items():
    return [
        {"key": "bit:a.fctb", "kind": "bit", "name": "synced bit",
         "action": "unchanged", "group": "lib", "record": {}, "diff": [],
         "detail": "in sync"},
        {"key": "bit:b.fctb", "kind": "bit", "name": "edited bit",
         "action": "push", "group": "lib", "record": {"internal": {"id": "r2"}},
         "diff": [{"field": "name", "local": "B", "server": "b",
                   "changed_by": "local"}], "detail": "changed locally"},
        {"key": "bit:c.fctb", "kind": "bit", "name": "conflicted bit",
         "action": "conflict", "group": "lib", "record": {"internal": {"id": "r3"}},
         "diff": [], "detail": "both sides"},
        {"key": "bit:d.fctb", "kind": "bit", "name": "new local bit",
         "action": "new_local", "group": None, "record": None, "diff": [],
         "detail": "upload"},
    ]


@pytest.mark.unit
def test_content_rows_drop_synced_and_tag_bands():
    rows = content_attention_rows(_plan_items())
    names = {r["name"] for r in rows}
    assert "synced bit" not in names            # healthy stays quiet
    assert names == {"edited bit", "conflicted bit", "new local bit"}
    edited = next(r for r in rows if r["name"] == "edited bit")
    assert edited["existence"] == EXIST_BOTH
    assert edited["sync_status"] == SYNC_MODIFIED
    assert edited["axis"] == "content"
    assert edited["direction"] == "push"
    new = next(r for r in rows if r["name"] == "new local bit")
    assert new["existence"] == EXIST_LOCAL_ONLY


@pytest.mark.unit
def test_content_status_severities():
    assert content_status_info("conflict")["severity"] == SEV_CRITICAL
    assert content_status_info("push")["severity"] == SEV_WARNING
    assert content_status_info("new_server")["severity"] == SEV_INFO
    # conflicts and deletions offer no one-tap direction
    assert content_status_info("conflict")["direction"] is None
    assert content_status_info("deleted_local")["direction"] is None
    assert content_status_info("new_server")["direction"] == "pull"


# -- machine-coverage axis ---------------------------------------------------

def _coverage():
    return {
        "applicable": True,
        "members": [
            {"tool_record_id": "rec-ok", "set_number": 1,
             "machine_tool_number": 1, "status": "in_sync",
             "collides": False, "collides_with": []},
            {"tool_record_id": "rec-absent", "set_number": 2,
             "machine_tool_number": None, "status": "absent_on_machine",
             "collides": False, "collides_with": []},
            {"tool_record_id": "rec-collide", "set_number": 3,
             "machine_tool_number": 7, "status": "number_mismatch",
             "collides": True, "collides_with": [4]},
        ],
        "slots": [
            {"tool_number": 9, "bound_instance_id": "rec-x",
             "status": "machine_only"},
        ],
        "summary": {},
    }


@pytest.mark.unit
def test_coverage_attention_keeps_only_actionable_rows():
    rows = coverage_attention_rows(_coverage(), set_name="Set A", set_id="s1")
    statuses = {r["status"] for r in rows}
    # in_sync (ok) and machine_only (info) are dropped; the rest stay
    assert "in_sync" not in statuses
    assert "machine_only" not in statuses
    assert "absent_on_machine" in statuses
    assert all(r["axis"] == "coverage" for r in rows)
    assert all(r["set_name"] == "Set A" and r["set_id"] == "s1" for r in rows)


@pytest.mark.unit
def test_unlinked_set_contributes_no_coverage_rows():
    assert coverage_attention_rows({"applicable": False}) == []


# -- aggregation -------------------------------------------------------------

@pytest.mark.unit
def test_needs_attention_merges_and_sorts_worst_first():
    content = content_attention_rows(_plan_items())
    coverage = coverage_attention_rows(_coverage(), set_name="Set A", set_id="s1")
    rows = needs_attention_rows(content, coverage)
    # both axes are present
    assert any(r["axis"] == "content" for r in rows)
    assert any(r["axis"] == "coverage" for r in rows)
    # the worst band leads (absent_on_machine + collision + conflict are all
    # critical), nothing critical trails an info row
    sev_seq = [r["severity"] for r in rows]
    first_info = next((i for i, s in enumerate(sev_seq)
                       if s == SEV_INFO), len(sev_seq))
    assert SEV_CRITICAL not in sev_seq[first_info:]


@pytest.mark.unit
def test_empty_when_everything_healthy():
    assert needs_attention_rows([], []) == []


# -- library rollup ----------------------------------------------------------

@pytest.mark.unit
def test_library_rollup_buckets_are_disjoint_and_total():
    items = [
        {"action": "unchanged"}, {"action": "unchanged"},
        {"action": "new_local"},
        {"action": "new_server"},
        {"action": "conflict"},
        {"action": "push"},               # modified
        {"action": "deleted_local"},      # deleted
    ]
    counts = library_rollup(items)
    assert counts["synced"] == 2
    assert counts["local_only"] == 1
    assert counts["server_only"] == 1
    assert counts["conflict"] == 1
    assert counts["modified"] == 1
    assert counts["deleted"] == 1
    assert counts["total"] == 7
    # every item lands in exactly one bucket
    assert (counts["synced"] + counts["local_only"] + counts["server_only"]
            + counts["conflict"] + counts["modified"] + counts["deleted"]
            == counts["total"])


@pytest.mark.unit
def test_rollup_line_terse_then_extends():
    line = library_rollup_line({"synced": 3, "local_only": 1, "server_only": 2,
                                "conflict": 0})
    assert line == "✓ 3 synced · ↑ 1 local-only · ↓ 2 server-only · ⚠ 0 conflicts"
    # modified/deleted only appear when non-zero
    assert "modified" not in line and "deleted" not in line
    line2 = library_rollup_line({"synced": 1, "modified": 2, "deleted": 1})
    assert "✎ 2 modified" in line2 and "✖ 1 deleted" in line2


# -- per-object resolution ---------------------------------------------------

@pytest.mark.unit
def test_resolution_rows_carry_provenance_for_known_fields():
    record = {"canonical": {
        "name": {"value": "6mm ball", "source": "asserted:human@web"},
        "geometry": {"diameter": {"value": 6.0, "source": "observed:probe@m1"}}}}
    item = {"record": record, "diff": [
        {"field": "name", "local": "6mm Ball", "server": "6mm ball",
         "changed_by": "server"},
        {"field": "parameter.Diameter", "local": "6 mm", "server": "6.0 mm",
         "changed_by": "server"},
        {"field": "attribute.Note", "local": "x", "server": "y",
         "changed_by": "local"}]}
    rows = resolution_rows(item)
    name_row = next(r for r in rows if r["field"] == "name")
    assert name_row["server_source"] == "asserted:human@web"
    assert name_row["local"] == "6mm Ball" and name_row["server"] == "6mm ball"
    dia_row = next(r for r in rows if r["field"] == "parameter.Diameter")
    assert dia_row["server_source"] == "observed:probe@m1"
    # an unmapped field simply has no provenance, never raises
    note_row = next(r for r in rows if r["field"] == "attribute.Note")
    assert note_row["server_source"] is None


@pytest.mark.unit
def test_field_provenance_tolerates_missing_record_and_fields():
    assert field_provenance(None, "name") is None
    assert field_provenance({"canonical": {}}, "name") is None
    assert field_provenance({"canonical": {}}, "unknown.field") is None


@pytest.mark.unit
def test_resolution_choices_map_to_apply_directions():
    keys = [k for k, _ in resolution_actions({})]
    assert keys == ["keep_local", "keep_server", "skip"]
    assert RESOLUTION_DECISION["keep_local"] == "push"
    assert RESOLUTION_DECISION["keep_server"] == "pull"
    assert RESOLUTION_DECISION["skip"] == "skip"
