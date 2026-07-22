# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the operator-lane adapter methods (inbox, tool-table entries, machines,
audit) and the call log that feeds the GUI's API-log debug panel.

As in test_client_sectioned, everything funnels through loobric's single
transport seam, so we inject a fake transport and assert the method, endpoint,
and body each method emits — proving the adapter speaks the frozen operator-lane
endpoints. Binding lives on ONE surface now; 'Adopt' is gone — minting a new tool
from an entry is just ``bind`` with no instance_id (REBOOT R2).
"""
import pytest

from freecad.Loobric import loobric
from freecad.Loobric.client import LoobricApi, LoobricError, HUMAN_ACTOR


def _api(transport):
    client = LoobricApi("https://loobric.example/", api_key="k", transport=transport)
    return client


@pytest.fixture
def api():
    """A LoobricApi over a fake transport recording every request; the canned
    response satisfies every unwrap the adapter does ('items', 'logs', …)."""
    captured = []

    def transport(method, endpoint, **kw):
        captured.append({"method": method, "endpoint": endpoint, "body": kw.get("body")})
        return {"items": [], "logs": [], "status": "ok",
                "internal": {"id": "rec-1"}, "instance_id": "inst-1", "deleted": "x"}

    client = _api(transport)
    client._captured = captured
    return client


def last(api):
    return api._captured[-1]


# -- Inbox -------------------------------------------------------------------

@pytest.mark.unit
def test_list_inbox_unwraps_items(api):
    assert api.list_inbox() == []
    c = last(api)
    assert c["method"] == "GET"
    assert c["endpoint"] == "/instance-inbox"


@pytest.mark.unit
def test_confirm_and_reject_proposal(api):
    api.confirm_proposal("p-1")
    c = last(api)
    assert c["method"] == "POST"
    assert c["endpoint"] == "/instance-inbox/p-1/confirm"
    assert c["body"] is None

    api.reject_proposal("p-1")
    assert last(api)["endpoint"] == "/instance-inbox/p-1/reject"


# -- Tool table entries ------------------------------------------------------

@pytest.mark.unit
def test_list_entries_optionally_filters_by_machine(api):
    api.list_entries()
    assert last(api)["endpoint"] == "/tool-table-entry-records"

    api.list_entries(machine_id="m-9")
    assert last(api)["endpoint"] == "/tool-table-entry-records?machine_id=m-9"


@pytest.mark.unit
def test_bind_entry_carries_instance_actor_and_move(api):
    api.bind_entry("entry-1", "inst-2")
    c = last(api)
    assert c["method"] == "POST"
    assert c["endpoint"] == "/tool-table-entry-records/entry-1/bind"
    # move is omitted unless requested (loobric's body shape)
    assert c["body"] == {"instance_id": "inst-2", "actor": HUMAN_ACTOR}

    api.bind_entry("entry-1", "inst-2", move=True)
    assert last(api)["body"]["move"] is True


@pytest.mark.unit
def test_bind_new_mints_by_omitting_instance_id(api):
    # 'Bind new' (was 'Adopt'): no instance_id tells the server to mint.
    api.bind_new("entry-1")
    c = last(api)
    assert c["endpoint"] == "/tool-table-entry-records/entry-1/bind"
    assert "instance_id" not in c["body"]
    assert c["body"] == {"actor": HUMAN_ACTOR}

    api.bind_new("entry-1", name="3mm drill")
    assert last(api)["body"] == {"name": "3mm drill", "actor": HUMAN_ACTOR}


@pytest.mark.unit
def test_unbind_and_delete_entry(api):
    api.unbind_entry("entry-1")
    c = last(api)
    assert c["method"] == "POST"
    assert c["endpoint"] == "/tool-table-entry-records/entry-1/unbind"
    assert c["body"] is None

    api.delete_entry("entry-1")
    c = last(api)
    assert c["method"] == "DELETE"
    assert c["endpoint"] == "/tool-table-entry-records/entry-1"


# -- Machines ----------------------------------------------------------------

@pytest.mark.unit
def test_list_get_delete_machine(api):
    api.list_machines()
    assert last(api)["endpoint"] == "/machine-records"

    api.get_machine("m-1")
    assert last(api)["endpoint"] == "/machine-records/m-1"

    api.delete_machine("m-1")
    c = last(api)
    assert c["method"] == "DELETE"
    assert c["endpoint"] == "/machine-records/m-1"


# -- Audit -------------------------------------------------------------------

@pytest.mark.unit
def test_list_audit_unwraps_logs_and_caps_client_side(api):
    assert api.list_audit(limit=25) == []
    c = last(api)
    assert c["method"] == "GET"
    assert c["endpoint"] == "/audit-logs"


# -- Call log (the API-log debug panel's data source) ------------------------

@pytest.mark.unit
def test_call_log_records_each_request_with_status(api):
    api.list_machines()
    api.confirm_proposal("p-1")
    assert len(api.call_log) == 2
    entry = api.call_log[-1]
    assert entry["method"] == "POST"
    assert entry["path"] == "/instance-inbox/p-1/confirm"
    assert entry["status"] == 200
    assert entry["error"] is None
    assert isinstance(entry["ms"], int)


@pytest.mark.unit
def test_call_log_records_failures_with_status():
    def boom(method, endpoint, **kw):
        raise loobric.HTTPError(409, "instance ... already installed")

    api = _api(boom)
    with pytest.raises(LoobricError):           # LoobricError == loobric.LoobricError
        api.bind_entry("entry-1", "inst-2")
    entry = api.call_log[-1]
    assert entry["status"] == 409
    assert "already installed" in entry["error"]


@pytest.mark.unit
def test_call_log_is_capped():
    api = _api(lambda *a, **k: {"items": []})
    api.CALL_LOG_LIMIT = 5
    for _ in range(20):
        api.list_machines()
    assert len(api.call_log) == 5
