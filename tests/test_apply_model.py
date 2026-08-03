# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the Sync tab's checkbox apply model and the provenance display
readers (viewmodel.py — pure, no FreeCAD/PySide, so they run headless).

The model is the fix for the per-row selector confusion: a row's direction is
DERIVED from its status (the Status and Action columns can never disagree),
and the user only chooses inclusion. Rows with no safe default — conflicts and
deletions — carry no checkbox at all: resolution is their only path.
"""
import pytest

from freecad.Loobric.viewmodel import (
    row_apply_info, force_options, apply_button_text, resolution_actions,
    RESOLUTION_DECISION, APPLY_LABELS, FORCED_LABELS, RESOLVE_LABEL,
    field_value, field_source, record_name, instance_shape, instance_diameter,
    short_id, fmt_dia)


# -- row_apply_info: direction derives from status ---------------------------

@pytest.mark.unit
def test_safe_rows_are_checked_with_a_fixed_direction():
    for action, direction in (("push", "push"), ("new_local", "push"),
                              ("pull", "pull"), ("new_server", "pull")):
        info = row_apply_info(action)
        assert info["checkable"] is True and info["checked"] is True
        assert info["direction"] == direction
        assert info["label"] == APPLY_LABELS[direction]


@pytest.mark.unit
def test_rows_with_no_safe_default_have_no_checkbox():
    """Conflicts and deletions are structurally different from safe rows: not
    checkable, never part of bulk Apply — the resolution view is their path."""
    for action in ("conflict", "deleted_local", "deleted_server"):
        info = row_apply_info(action)
        assert info["checkable"] is False and info["checked"] is False
        assert info["direction"] is None
        assert info["label"] == RESOLVE_LABEL


@pytest.mark.unit
def test_informational_rows_take_no_part_in_apply():
    for action in ("unchanged", "note", "job_set"):
        info = row_apply_info(action)
        assert info["checkable"] is False
        assert info["label"] == ""


@pytest.mark.unit
def test_unknown_action_degrades_to_resolve_never_raises():
    info = row_apply_info("future_action")
    assert info["checkable"] is False
    assert info["label"] == RESOLVE_LABEL


# -- force_options: the deliberate against-the-suggestion overrides ----------

@pytest.mark.unit
def test_force_offered_only_against_a_modified_row():
    # 'changed here' can be forced to download (discarding local edits)...
    assert [d for d, _ in force_options("push", True, True)] == ["pull"]
    # ...and 'changed on server' to upload (overwriting the server).
    assert [d for d, _ in force_options("pull", True, True)] == ["push"]


@pytest.mark.unit
def test_force_needs_the_other_side_to_exist():
    assert force_options("push", True, False) == []   # nothing on server
    assert force_options("pull", False, True) == []   # nothing local


@pytest.mark.unit
def test_creates_conflicts_and_deletions_offer_no_force():
    for action in ("new_local", "new_server", "conflict",
                   "deleted_local", "deleted_server", "unchanged", "job_set"):
        assert force_options(action, True, True) == []


@pytest.mark.unit
def test_forced_labels_exist_for_both_directions():
    assert set(FORCED_LABELS) == {"push", "pull"}


# -- apply_button_text: the button states its plan ---------------------------

@pytest.mark.unit
def test_apply_button_states_the_plan():
    assert apply_button_text(0, 0) == "Apply"
    assert apply_button_text(1, 0) == "Apply (1 upload)"
    assert apply_button_text(3, 2) == "Apply (3 uploads, 2 downloads)"
    assert apply_button_text(0, 1) == "Apply (1 download)"


# -- resolution_actions: deletion rows say what actually happens -------------

@pytest.mark.unit
def test_resolution_actions_deletion_labels_are_concrete():
    labels = dict(resolution_actions({"action": "deleted_local"}))
    assert labels["keep_local"] == "Delete on server too"
    assert labels["keep_server"] == "Restore from server"
    labels = dict(resolution_actions({"action": "deleted_server"}))
    assert labels["keep_local"] == "Upload again (restore)"
    assert labels["keep_server"] == "Delete local file too"


@pytest.mark.unit
def test_resolution_actions_changed_rows_keep_the_side_labels():
    labels = dict(resolution_actions({"action": "conflict"}))
    assert labels["keep_local"] == "Keep Local (upload)"
    assert labels["keep_server"] == "Keep Server (download)"
    # every offered choice maps onto an apply decision
    for key, _ in resolution_actions({"action": "conflict"}):
        assert key in RESOLUTION_DECISION


# -- provenance / display readers -------------------------------------------

@pytest.mark.unit
def test_field_value_and_source_tolerate_bare_and_wrapped():
    assert field_value({"value": 6.0, "source": "observed:x@y"}) == 6.0
    assert field_value(6.0) == 6.0
    assert field_value(None) is None
    assert field_source({"value": 6.0, "source": "observed:x@y"}) == "observed:x@y"
    assert field_source(6.0) is None


@pytest.mark.unit
def test_record_name_prefers_canonical_then_falls_back():
    rec = {"internal": {"id": "abcdef123456"},
           "canonical": {"name": {"value": "6mm ball", "source": "asserted:human@web"}},
           "clients": {}}
    assert record_name(rec) == "6mm ball"

    # no canonical name -> the freecad client section's fctl_label / client_item_id
    rec2 = {"internal": {"id": "abcdef123456"}, "canonical": {},
            "clients": {"freecad": {"client_item_id": "probe.fctb", "data": {}}}}
    assert record_name(rec2) == "probe.fctb"

    rec3 = {"internal": {"id": "abcdef123456"},
            "clients": {"freecad": {"data": {"fctl_label": "default"}}}}
    assert record_name(rec3) == "default"

    # nothing at all -> the short id
    rec4 = {"internal": {"id": "abcdef123456"}, "canonical": {}, "clients": {}}
    assert record_name(rec4) == "abcdef12"


@pytest.mark.unit
def test_instance_geometry_and_id_readers():
    rec = {"internal": {"id": "deadbeefcafe"},
           "canonical": {"geometry": {
               "shape": {"value": "probe", "source": "asserted:human@web"},
               "diameter": {"value": 3.0, "source": "observed:x@y", "unit": "mm"}}}}
    assert instance_shape(rec) == "probe"
    assert instance_diameter(rec) == 3.0
    assert short_id(rec) == "deadbeef"
    assert fmt_dia(3.0) == "3 mm"
    assert fmt_dia(None) == "—"
