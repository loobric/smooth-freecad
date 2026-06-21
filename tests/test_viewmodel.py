# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the GUI view-model (pure, headless): the two render builders the
widgets consume — :func:`sync_tree` (the Sync tab's grouped plan, with the
'needs attention' filter as a view over the one plan) and :func:`machine_tables`
(the Machines tab's per-machine tool tables with pending bindings folded in) —
plus the rollup and resolution helpers.
"""
import pytest

from freecad.Smooth import viewmodel as vm


# -- helpers to build plan items / records -----------------------------------

def _bit(name, action, group=None, key=None):
    return {"key": key or ("bit:%s" % name), "kind": "bit", "name": name,
            "action": action, "group": group, "record": None, "diff": []}


def _lib(name, group, action="unchanged"):
    return {"key": "lib:%s" % name, "kind": "library", "name": name,
            "action": action, "group": group, "record": None, "diff": []}


def _field(value, source="observed:linuxcnc@mill"):
    return {"value": value, "source": source}


# -- sync_tree ---------------------------------------------------------------

@pytest.mark.unit
def test_sync_tree_groups_bits_under_their_tool_set():
    items = [
        _lib("Drawer A", group="default.fctl"),
        _bit("downcut", "unchanged", group="default.fctl"),
        _bit("vee", "push", group="default.fctl"),
        _bit("orphan", "new_local", group=None),
    ]
    tree = vm.sync_tree(items)
    assert tree["pending"] == 2          # vee (push) + orphan (new_local)
    names = [g["name"] for g in tree["groups"]]
    assert names == ["Drawer A", "Not in any tool set"]
    drawer = tree["groups"][0]
    assert [m["name"] for m in drawer["members"]] == ["downcut", "vee"]
    assert "✓ 1 synced" in drawer["rollup"] and "↑ 1 local-only" not in drawer["rollup"]


@pytest.mark.unit
def test_sync_tree_attention_filter_drops_in_sync_rows_and_empty_groups():
    items = [
        _lib("Clean", group="clean.fctl"),
        _bit("a", "unchanged", group="clean.fctl"),     # in sync -> dropped
        _lib("Dirty", group="dirty.fctl"),
        _bit("b", "conflict", group="dirty.fctl"),      # kept
        _bit("c", "unchanged", group="dirty.fctl"),     # dropped
    ]
    tree = vm.sync_tree(items, attention_only=True)
    groups = {g["name"]: g for g in tree["groups"]}
    assert "Clean" not in groups                        # whole group is in sync
    assert list(groups) == ["Dirty"]
    assert [m["name"] for m in groups["Dirty"]["members"]] == ["b"]
    assert tree["pending"] == 1


@pytest.mark.unit
def test_row_status_info_and_is_exception():
    assert vm.is_exception("push") is True
    assert vm.is_exception("unchanged") is False
    assert vm.row_status_info("pull")["direction"] == "pull"
    assert vm.row_status_info("conflict")["direction"] is None
    # an unknown action degrades, never raises
    assert vm.row_status_info("future")["direction"] is None


# -- catalog_rows ------------------------------------------------------------

def _catalog(cid, name, manufacturer=None, product_code=None, diameter=None,
             name_source="asserted:human@web"):
    canon = {}
    if name is not None:
        canon["name"] = {"value": name, "source": name_source}
    if manufacturer is not None:
        canon["manufacturer"] = _field(manufacturer, "asserted:human@web")
    if product_code is not None:
        canon["product_code"] = _field(product_code, "asserted:human@web")
    if diameter is not None:
        canon["geometry"] = {"diameter": {"value": diameter, "unit": "mm",
                                          "source": "asserted:human@web"}}
    return {"internal": {"id": cid}, "canonical": canon, "clients": {}}


@pytest.mark.unit
def test_catalog_rows_extracts_fields_and_provenance():
    [row] = vm.catalog_rows([
        _catalog("c1", "1/4 Downcut", "Acme", "B201", 6.35)])
    assert row["id"] == "c1"
    assert row["name"] == "1/4 Downcut"
    assert row["manufacturer"] == "Acme"
    assert row["product_code"] == "B201"
    assert "6.35" in row["geometry"] and "mm" in row["geometry"]
    assert row["source"] == "asserted:human@web"      # provenance stays visible


@pytest.mark.unit
def test_catalog_rows_honest_sparse_no_blanks_as_zero():
    """A sparse record renders missing fields as the marker, never blank, never a
    fabricated 0 diameter."""
    [row] = vm.catalog_rows([_catalog("c2", "Mystery")])  # no mfr/code/dia
    assert row["name"] == "Mystery"
    assert row["manufacturer"] == vm.MISSING
    assert row["product_code"] == vm.MISSING
    assert row["geometry"] == vm.MISSING               # not "0", not blank
    assert "0" not in row["geometry"]


# -- machine_tables ----------------------------------------------------------

def _machine(mid, name, controller):
    return {"internal": {"id": mid},
            "canonical": {"name": _field(name, "asserted:human@freecad"),
                          "controller_type": _field(controller)}}


def _entry(mid, eid, tnum, desc, dia, bound=None):
    canon = {"tool_number": _field(tnum), "description": _field(desc),
             "offsets": {"diameter": _field(dia)},
             "bound_instance_id": _field(bound,
                                         "asserted:human@inbox" if bound else "unknown")}
    return {"internal": {"id": eid, "machine_id": mid}, "canonical": canon}


@pytest.mark.unit
def test_machine_tables_joins_bound_name_and_pending_proposal():
    machines = [_machine("m1", "millstone", "linuxcnc")]
    entries = [
        _entry("m1", "e3", 3, "1/4 downcut", 6.35, bound="i1"),
        _entry("m1", "e7", 7, "vee", 6.0, bound=None),
    ]
    instances = [{"internal": {"id": "i1"},
                  "canonical": {"name": _field("1/4 downcut", "asserted:human@web")}}]
    proposals = [{"id": "p1", "entry": {"id": "e7"},
                  "proposed_instance": {"id": "i9", "name": "vee bit", "diameter": 6.0},
                  "confidence": 0.92, "reason": "same diameter"}]

    model = vm.machine_tables(machines, entries, instances, proposals)
    assert model["pending"] == 1
    [mach] = model["machines"]
    assert mach["name"] == "millstone" and mach["controller"] == "linuxcnc"
    e3, e7 = mach["entries"]
    assert e3["tool_number"] == 3 and e3["bound_name"] == "1/4 downcut"
    assert e3["proposal"] is None                       # bound -> no proposal
    assert e7["bound_name"] is None
    assert e7["proposal"]["name"] == "vee bit"
    assert e7["proposal"]["proposal_id"] == "p1"
    assert e7["proposal"]["confidence"] == 0.92


@pytest.mark.unit
def test_machine_tables_orders_entries_by_tool_number():
    machines = [_machine("m1", "mill", "linuxcnc")]
    entries = [_entry("m1", "e", 9, "i", 1.0), _entry("m1", "e2", 2, "ii", 2.0)]
    model = vm.machine_tables(machines, entries, [], [])
    assert [e["tool_number"] for e in model["machines"][0]["entries"]] == [2, 9]
    assert model["pending"] == 0


@pytest.mark.unit
def test_machine_tables_proposal_ignored_for_bound_entry():
    machines = [_machine("m1", "mill", "linuxcnc")]
    entries = [_entry("m1", "e3", 3, "x", 6.0, bound="i1")]
    proposals = [{"id": "p1", "entry": {"id": "e3"},
                  "proposed_instance": {"name": "z"}, "confidence": 1.0}]
    model = vm.machine_tables(machines, entries, [], proposals)
    assert model["pending"] == 0
    assert model["machines"][0]["entries"][0]["proposal"] is None


# -- resolution rows ---------------------------------------------------------

@pytest.mark.unit
def test_resolution_rows_carry_server_provenance():
    record = {"canonical": {"geometry": {"shape": _field("endmill", "asserted:human@web")}}}
    item = {"record": record,
            "diff": [{"field": "shape", "local": "drill", "server": "endmill",
                      "changed_by": "local"}]}
    [row] = vm.resolution_rows(item)
    assert row["field"] == "shape" and row["changed_by"] == "local"
    assert row["server_source"] == "asserted:human@web"
