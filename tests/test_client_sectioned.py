# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the FreeCAD API adapter (``LoobricApi``, a thin loobric.Client subclass).

Everything funnels through loobric's single transport seam, so we inject a fake
transport and assert the method, endpoint, and body each adapter method emits —
proving the FreeCAD sync lane speaks the frozen sectioned endpoints (create /
get / list / section write / assert / members) and stays in its lane (a section
write never carries internal/canonical).
"""
import pytest

from freecad.Loobric.client import LoobricApi, CLIENT_NAME, CLIENT_VERSION


@pytest.fixture
def api():
    """A LoobricApi over a fake transport that records every request and returns
    a canned record-shaped response."""
    captured = []

    def transport(method, endpoint, **kw):
        captured.append({"method": method, "endpoint": endpoint, "body": kw.get("body")})
        return {"items": [], "internal": {"id": "rec-1"}}

    client = LoobricApi("https://loobric.example/", api_key="k", transport=transport)
    client._captured = captured
    return client


def last(api):
    return api._captured[-1]


# -- ToolInstanceRecords -----------------------------------------------------

@pytest.mark.unit
def test_create_instance_posts_section_with_client_in_body(api):
    api.create_instance(data={"fctb": {"name": "x"}}, client_item_id="x.fctb")
    c = last(api)
    assert c["method"] == "POST"
    assert c["endpoint"] == "/tool-instance-records"
    assert c["body"] == {"client": CLIENT_NAME, "client_version": CLIENT_VERSION,
                         "client_item_id": "x.fctb", "data": {"fctb": {"name": "x"}}}


@pytest.mark.unit
def test_create_instance_bare_sends_empty_body(api):
    # A bare create still sends an (empty) JSON body — the create endpoint
    # requires one; `if body` once wrongly dropped {} (the dogfooding fix).
    api.create_instance()
    assert last(api)["body"] == {}


@pytest.mark.unit
def test_get_and_list_instances(api):
    api.get_instance("rec-9")
    c = last(api)
    assert c["method"] == "GET"
    assert c["endpoint"] == "/tool-instance-records/rec-9"

    assert api.list_instances() == []          # unwraps {"items": [...]}
    assert last(api)["endpoint"] == "/tool-instance-records"


@pytest.mark.unit
def test_put_instance_section_carries_client_in_path_not_body(api):
    api.put_instance_section("rec-9", {"fctb": {}}, client_item_id="x.fctb")
    c = last(api)
    assert c["method"] == "PUT"
    assert c["endpoint"] == "/tool-instance-records/rec-9/clients/freecad"
    # the section write must NOT carry internal/canonical/client (lane discipline)
    assert set(c["body"]) == {"client_version", "client_item_id", "data"}


@pytest.mark.unit
def test_assert_instance_hits_the_assert_door(api):
    api.assert_instance("rec-9", "geometry.shape", "probe")
    c = last(api)
    assert c["method"] == "POST"
    assert c["endpoint"] == "/tool-instance-records/rec-9/assert"
    assert c["body"] == {"path": "geometry.shape", "value": "probe",
                         "actor": CLIENT_NAME}


@pytest.mark.unit
def test_assert_instance_fields_applies_each(api):
    api.assert_instance_fields(
        "rec-9", [("name", "Probe"), ("geometry.shape", "probe"),
                  ("geometry.diameter", 3.0)])
    asserted = [c["body"] for c in api._captured if c["endpoint"].endswith("/assert")]
    assert [b["path"] for b in asserted] == [
        "name", "geometry.shape", "geometry.diameter"]
    assert [b["value"] for b in asserted] == ["Probe", "probe", 3.0]


# -- ToolSets ----------------------------------------------------------------

@pytest.mark.unit
def test_create_and_section_for_sets(api):
    api.create_set(data={"fctl_label": "default", "version": 1})
    c = last(api)
    assert c["method"] == "POST"
    assert c["endpoint"] == "/tool-set-records"
    assert c["body"]["client"] == CLIENT_NAME

    api.put_set_section("set-1", {"fctl_label": "default"})
    assert last(api)["endpoint"] == "/tool-set-records/set-1/clients/freecad"


@pytest.mark.unit
def test_assert_set_members_and_setups(api):
    api.assert_set("set-1", "name", "millstone tools")
    assert last(api)["endpoint"] == "/tool-set-records/set-1/assert"
    assert last(api)["body"]["path"] == "name"

    api.set_members("set-1", [{"tool_record_id": "rec-1", "number": 1}])
    c = last(api)
    assert c["method"] == "POST"
    assert c["endpoint"] == "/tool-set-records/set-1/members"
    assert c["body"]["members"] == [{"tool_record_id": "rec-1", "number": 1}]

    # The machine relationship is a SETUP (machine_set_maps), operator-owned;
    # the CAM side only READS it (MAPPING_PLAN): active rows + the derived view.
    api.active_setups()
    c = last(api)
    assert c["method"] == "GET"
    assert c["endpoint"] == "/machine-set-maps?status=active"

    api.setup_view("mach-1")
    c = last(api)
    assert c["method"] == "GET"
    assert c["endpoint"] == "/machine-set-maps/status?machine_id=mach-1"
