# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the sectioned-schema API client.

Everything funnels through the single seam http_json(), so we stub exactly
that and assert the method, URL, and body each method emits — proving the
client speaks the frozen sectioned endpoints (create / get / list / section
write / assert / members / reconcile) and stays in its lane.
"""
import pytest

from freecad.Smooth import client as client_mod
from freecad.Smooth.client import SmoothClient, CLIENT_NAME, CLIENT_VERSION


@pytest.fixture
def calls(monkeypatch):
    """Capture every http_json call; return canned record-shaped responses."""
    captured = []

    def fake_http_json(method, url, api_key, body=None, timeout=None):
        captured.append({"method": method, "url": url, "body": body})
        return {"items": [], "internal": {"id": "rec-1"}}

    monkeypatch.setattr(client_mod, "http_json", fake_http_json)
    return captured


@pytest.fixture
def smooth():
    return SmoothClient("https://smooth.example/", api_key="k")


def last(calls):
    return calls[-1]


# -- ToolInstanceRecords -----------------------------------------------------

@pytest.mark.unit
def test_create_instance_posts_section_with_client_in_body(smooth, calls):
    smooth.create_instance(data={"fctb": {"name": "x"}}, client_item_id="x.fctb")
    c = last(calls)
    assert c["method"] == "POST"
    assert c["url"].endswith("/api/v1/tool-instance-records")
    assert c["body"] == {"client": CLIENT_NAME, "client_version": CLIENT_VERSION,
                         "client_item_id": "x.fctb", "data": {"fctb": {"name": "x"}}}


@pytest.mark.unit
def test_create_instance_bare_sends_no_body(smooth, calls):
    smooth.create_instance()
    assert last(calls)["body"] is None


@pytest.mark.unit
def test_get_and_list_instances(smooth, calls):
    smooth.get_instance("rec-9")
    c = last(calls)
    assert c["method"] == "GET"
    assert c["url"].endswith("/tool-instance-records/rec-9")

    assert smooth.list_instances() == []          # unwraps {"items": [...]}
    assert last(calls)["url"].endswith("/tool-instance-records")


@pytest.mark.unit
def test_put_instance_section_carries_client_in_path_not_body(smooth, calls):
    smooth.put_instance_section("rec-9", {"fctb": {}}, client_item_id="x.fctb")
    c = last(calls)
    assert c["method"] == "PUT"
    assert c["url"].endswith("/tool-instance-records/rec-9/clients/freecad")
    # the section write must NOT carry internal/canonical/client (lane discipline)
    assert set(c["body"]) == {"client_version", "client_item_id", "data"}


@pytest.mark.unit
def test_assert_instance_hits_the_assert_door(smooth, calls):
    smooth.assert_instance("rec-9", "geometry.shape", "probe")
    c = last(calls)
    assert c["method"] == "POST"
    assert c["url"].endswith("/tool-instance-records/rec-9/assert")
    assert c["body"] == {"path": "geometry.shape", "value": "probe",
                         "actor": CLIENT_NAME}


@pytest.mark.unit
def test_assert_instance_fields_applies_each(smooth, calls):
    smooth.assert_instance_fields(
        "rec-9", [("name", "Probe"), ("geometry.shape", "probe"),
                  ("geometry.diameter", 3.0)])
    asserted = [c["body"] for c in calls if c["url"].endswith("/assert")]
    assert [b["path"] for b in asserted] == [
        "name", "geometry.shape", "geometry.diameter"]
    assert [b["value"] for b in asserted] == ["Probe", "probe", 3.0]


# -- ToolSets ----------------------------------------------------------------

@pytest.mark.unit
def test_create_and_section_for_sets(smooth, calls):
    smooth.create_set(data={"fctl_label": "default", "version": 1})
    c = last(calls)
    assert c["method"] == "POST"
    assert c["url"].endswith("/api/v1/tool-set-records")
    assert c["body"]["client"] == CLIENT_NAME

    smooth.put_set_section("set-1", {"fctl_label": "default"})
    assert last(calls)["url"].endswith("/tool-set-records/set-1/clients/freecad")


@pytest.mark.unit
def test_assert_set_and_members_and_reconcile(smooth, calls):
    smooth.assert_set("set-1", "name", "millstone tools")
    assert last(calls)["url"].endswith("/tool-set-records/set-1/assert")
    assert last(calls)["body"]["path"] == "name"

    smooth.set_members("set-1", [{"tool_record_id": "rec-1", "number": 1}])
    c = last(calls)
    assert c["method"] == "POST"
    assert c["url"].endswith("/tool-set-records/set-1/members")
    assert c["body"] == {"actor": CLIENT_NAME,
                         "members": [{"tool_record_id": "rec-1", "number": 1}]}

    smooth.reconcile_set("set-1")
    c = last(calls)
    assert c["method"] == "POST"
    assert c["url"].endswith("/tool-set-records/set-1/reconcile")
    assert c["body"] is None
