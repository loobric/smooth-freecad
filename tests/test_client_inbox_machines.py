# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the operator-lane client methods (inbox, slots, machines, audit) and
the call log that feeds the GUI's API-log debug panel.

As in test_client_sectioned, everything funnels through the single seam
http_json(), so we stub exactly that and assert the method, URL, and body each
method emits — proving the client speaks the frozen operator-lane endpoints.
"""
import pytest

from freecad.Smooth import client as client_mod
from freecad.Smooth.client import SmoothClient, SmoothError, HUMAN_ACTOR


@pytest.fixture
def calls(monkeypatch):
    """Capture every http_json call; return a canned response that satisfies
    every unwrap the client does ('items', 'logs', and action envelopes)."""
    captured = []

    def fake_http_json(method, url, api_key, body=None, timeout=None):
        captured.append({"method": method, "url": url, "body": body})
        return {"items": [], "logs": [], "status": "ok",
                "internal": {"id": "rec-1"}, "instance_id": "inst-1",
                "slot": {}, "deleted": "x", "unreconciled": []}

    monkeypatch.setattr(client_mod, "http_json", fake_http_json)
    return captured


@pytest.fixture
def smooth():
    return SmoothClient("https://smooth.example/", api_key="k")


def last(calls):
    return calls[-1]


# -- Inbox -------------------------------------------------------------------

@pytest.mark.unit
def test_list_inbox_unwraps_items(smooth, calls):
    assert smooth.list_inbox() == []
    c = last(calls)
    assert c["method"] == "GET"
    assert c["url"].endswith("/api/v1/instance-inbox")


@pytest.mark.unit
def test_confirm_and_reject_proposal(smooth, calls):
    smooth.confirm_proposal("p-1")
    c = last(calls)
    assert c["method"] == "POST"
    assert c["url"].endswith("/instance-inbox/p-1/confirm")
    assert c["body"] is None

    smooth.reject_proposal("p-1")
    assert last(calls)["url"].endswith("/instance-inbox/p-1/reject")


# -- Tool table entries / slots ----------------------------------------------

@pytest.mark.unit
def test_list_entries_optionally_filters_by_machine(smooth, calls):
    smooth.list_entries()
    assert last(calls)["url"].endswith("/tool-table-entry-records")

    smooth.list_entries(machine_id="m-9")
    assert last(calls)["url"].endswith("/tool-table-entry-records?machine_id=m-9")


@pytest.mark.unit
def test_bind_entry_carries_instance_actor_and_move(smooth, calls):
    smooth.bind_entry("slot-1", "inst-2")
    c = last(calls)
    assert c["method"] == "POST"
    assert c["url"].endswith("/tool-table-entry-records/slot-1/bind")
    assert c["body"] == {"instance_id": "inst-2", "actor": HUMAN_ACTOR,
                         "move": False}

    smooth.bind_entry("slot-1", "inst-2", move=True)
    assert last(calls)["body"]["move"] is True


@pytest.mark.unit
def test_unbind_and_delete_entry(smooth, calls):
    smooth.unbind_entry("slot-1")
    c = last(calls)
    assert c["method"] == "POST"
    assert c["url"].endswith("/tool-table-entry-records/slot-1/unbind")
    assert c["body"] is None

    smooth.delete_entry("slot-1")
    c = last(calls)
    assert c["method"] == "DELETE"
    assert c["url"].endswith("/tool-table-entry-records/slot-1")


@pytest.mark.unit
def test_adopt_entry_omits_name_unless_given(smooth, calls):
    smooth.adopt_entry("slot-1")
    c = last(calls)
    assert c["url"].endswith("/tool-table-entry-records/slot-1/adopt")
    assert c["body"] == {"actor": HUMAN_ACTOR}            # no name key

    smooth.adopt_entry("slot-1", name="3mm drill")
    assert last(calls)["body"] == {"actor": HUMAN_ACTOR, "name": "3mm drill"}


# -- Machines ----------------------------------------------------------------

@pytest.mark.unit
def test_list_get_delete_machine(smooth, calls):
    smooth.list_machines()
    assert last(calls)["url"].endswith("/machine-records")

    smooth.get_machine("m-1")
    assert last(calls)["url"].endswith("/machine-records/m-1")

    smooth.delete_machine("m-1")
    c = last(calls)
    assert c["method"] == "DELETE"
    assert c["url"].endswith("/machine-records/m-1")


# -- Audit -------------------------------------------------------------------

@pytest.mark.unit
def test_list_audit_unwraps_logs_with_limit(smooth, calls):
    assert smooth.list_audit(limit=25) == []
    c = last(calls)
    assert c["method"] == "GET"
    assert c["url"].endswith("/audit-logs?limit=25")


# -- Call log (the API-log debug panel's data source) ------------------------

@pytest.mark.unit
def test_call_log_records_each_request_with_status(smooth, calls):
    smooth.list_machines()
    smooth.confirm_proposal("p-1")
    assert len(smooth.call_log) == 2
    entry = smooth.call_log[-1]
    assert entry["method"] == "POST"
    assert entry["path"] == "/instance-inbox/p-1/confirm"
    assert entry["status"] == 200
    assert entry["error"] is None
    assert isinstance(entry["ms"], int)


@pytest.mark.unit
def test_call_log_records_failures_with_status(monkeypatch, smooth):
    def boom(method, url, api_key, body=None, timeout=None):
        raise SmoothError("HTTP 409 ... already installed", status=409)

    monkeypatch.setattr(client_mod, "http_json", boom)
    with pytest.raises(SmoothError):
        smooth.bind_entry("slot-1", "inst-2")
    entry = smooth.call_log[-1]
    assert entry["status"] == 409
    assert "already installed" in entry["error"]


@pytest.mark.unit
def test_call_log_is_capped(monkeypatch, smooth):
    monkeypatch.setattr(client_mod, "http_json",
                        lambda *a, **k: {"items": []})
    smooth.CALL_LOG_LIMIT = 5
    for _ in range(20):
        smooth.list_machines()
    assert len(smooth.call_log) == 5
