# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for tool-set ↔ machine coverage: the client methods that fetch the
report and link a set to a machine, plus the pure uihelpers logic that turns
the ``/coverage`` JSON into display rows.

The client tests funnel through the single ``http_json`` seam (the same pattern
as test_client_inbox_machines) so they assert the exact method/URL/body the
frozen endpoint expects. The uihelpers tests are pure (no FreeCAD/PySide), so
they run headless and pin the presentation contract the Coverage tab relies on
— above all that ``absent_on_machine`` is surfaced as the worst, leading row.
"""
import pytest

from freecad.Smooth import client as client_mod
from freecad.Smooth.client import SmoothClient, HUMAN_ACTOR
from freecad.Smooth.uihelpers import (
    coverage_rows, coverage_summary_line, coverage_is_applicable,
    coverage_status_info, SEV_CRITICAL, SEV_WARNING, SEV_INFO, SEV_OK)


# -- client: get_set_coverage / link_set_machine -----------------------------

@pytest.fixture
def calls(monkeypatch):
    captured = []

    def fake_http_json(method, url, api_key, body=None, timeout=None):
        captured.append({"method": method, "url": url, "body": body})
        return {"items": [], "applicable": False, "internal": {"id": "set-1"}}

    monkeypatch.setattr(client_mod, "http_json", fake_http_json)
    return captured


@pytest.fixture
def smooth():
    return SmoothClient("https://smooth.example/", api_key="k")


@pytest.mark.unit
def test_get_set_coverage_hits_the_coverage_endpoint(smooth, calls):
    smooth.get_set_coverage("set-9")
    c = calls[-1]
    assert c["method"] == "GET"
    assert c["url"].endswith("/tool-set-records/set-9/coverage")
    assert c["body"] is None


@pytest.mark.unit
def test_link_set_machine_asserts_machine_id_as_human(smooth, calls):
    smooth.link_set_machine("set-9", "machine-2")
    c = calls[-1]
    assert c["method"] == "POST"
    assert c["url"].endswith("/tool-set-records/set-9/assert")
    assert c["body"] == {"path": "machine_id", "value": "machine-2",
                         "actor": HUMAN_ACTOR}


# -- pure logic: applicability + summary -------------------------------------

@pytest.mark.unit
def test_unlinked_coverage_is_not_applicable_and_yields_no_rows():
    cov = {"set_id": "s1", "machine_id": None, "applicable": False,
           "reason": "set is not linked to a machine"}
    assert coverage_is_applicable(cov) is False
    assert coverage_rows(cov) == []
    # the summary surfaces the server's reason verbatim
    assert coverage_summary_line(cov) == "set is not linked to a machine"


@pytest.mark.unit
def test_summary_leads_with_tools_not_on_machine():
    cov = {"applicable": True, "summary": {
        "total_members": 4, "total_slots": 5, "in_sync": 1,
        "number_mismatch": 1, "absent_on_machine": 2, "number_collision": 0,
        "machine_only": 1, "unbound_slot": 1}}
    line = coverage_summary_line(cov)
    assert line.startswith("2 not on machine")
    assert "1 T-number mismatch(es)" in line
    assert "1 in sync" in line
    assert "machine-only" in line and "empty slot" in line


# -- pure logic: status presentation -----------------------------------------

@pytest.mark.unit
def test_status_info_severities():
    assert coverage_status_info("in_sync")["severity"] == SEV_OK
    assert coverage_status_info("number_mismatch")["severity"] == SEV_WARNING
    assert coverage_status_info("absent_on_machine")["severity"] == SEV_CRITICAL
    assert coverage_status_info("machine_only")["severity"] == SEV_INFO
    assert coverage_status_info("unbound_slot")["severity"] == SEV_INFO


@pytest.mark.unit
def test_collision_outranks_member_status_as_critical():
    # even an in_sync member is critical when it collides
    info = coverage_status_info("in_sync", collides=True)
    assert info["severity"] == SEV_CRITICAL
    assert "collision" in info["label"].lower()


@pytest.mark.unit
def test_unknown_status_degrades_to_info_without_raising():
    info = coverage_status_info("some_future_status")
    assert info["severity"] == SEV_INFO


# -- pure logic: row flattening, ordering, and naming ------------------------

def _full_coverage():
    return {
        "set_id": "s1", "machine_id": "m1", "applicable": True,
        "members": [
            {"tool_record_id": "rec-ok", "set_number": 1,
             "machine_tool_number": 1, "slot_id": "sl1",
             "status": "in_sync", "collides": False, "collides_with": []},
            {"tool_record_id": "rec-absent", "set_number": 2,
             "machine_tool_number": None, "slot_id": None,
             "status": "absent_on_machine", "collides": False,
             "collides_with": []},
            {"tool_record_id": "rec-mismatch", "set_number": 3,
             "machine_tool_number": 7, "slot_id": "sl7",
             "status": "number_mismatch", "collides": False,
             "collides_with": []},
            {"tool_record_id": "rec-collide", "set_number": 4,
             "machine_tool_number": 7, "slot_id": "sl7b",
             "status": "number_mismatch", "collides": True,
             "collides_with": [3]},
        ],
        "slots": [
            {"slot_id": "sl9", "tool_number": 9,
             "bound_instance_id": "rec-extra", "status": "machine_only"},
            {"slot_id": "sl10", "tool_number": 10,
             "bound_instance_id": None, "status": "unbound_slot"},
        ],
        "summary": {},
    }


@pytest.mark.unit
def test_rows_are_sorted_worst_first():
    rows = coverage_rows(_full_coverage())
    # critical rows (absent + collision) lead; ok/info trail
    assert rows[0]["severity"] == SEV_CRITICAL
    assert {r["severity"] for r in rows[:2]} == {SEV_CRITICAL}
    assert rows[-1]["severity"] in (SEV_INFO, SEV_OK)
    # the in_sync member is present and marked ok
    ok = [r for r in rows if r["status"] == "in_sync"]
    assert ok and ok[0]["severity"] == SEV_OK


@pytest.mark.unit
def test_absent_on_machine_row_is_critical_and_has_no_machine_number():
    rows = coverage_rows(_full_coverage())
    absent = next(r for r in rows if r["status"] == "absent_on_machine")
    assert absent["severity"] == SEV_CRITICAL
    assert absent["tnum"] == 2
    assert absent["machine_tnum"] is None
    assert "NOT on machine" in absent["label"]


@pytest.mark.unit
def test_collision_row_carries_the_colliding_numbers():
    rows = coverage_rows(_full_coverage())
    collide = next(r for r in rows if r["collides"])
    assert collide["severity"] == SEV_CRITICAL
    assert collide["collides_with"] == [3]


@pytest.mark.unit
def test_names_resolve_when_provided_else_short_id():
    names = {"rec-absent": "5mm carbide drill"}
    rows = coverage_rows(_full_coverage(), names=names)
    absent = next(r for r in rows if r["status"] == "absent_on_machine")
    assert absent["name"] == "5mm carbide drill"
    # an unresolved id falls back to its short form, never crashes
    ok = next(r for r in rows if r["status"] == "in_sync")
    assert ok["name"] == "rec-ok"[:8]


@pytest.mark.unit
def test_slots_become_info_rows():
    rows = coverage_rows(_full_coverage())
    machine_only = next(r for r in rows if r["status"] == "machine_only")
    assert machine_only["is_slot"] is True
    assert machine_only["severity"] == SEV_INFO
    empty = next(r for r in rows if r["status"] == "unbound_slot")
    assert empty["name"] == "(empty)"
