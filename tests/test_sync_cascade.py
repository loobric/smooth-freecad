# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the sync-tab bulk-action cascade and the provenance display readers
(viewmodel.py — pure, no FreeCAD/PySide, so they run headless).

The cascade is the fix for the original complaint: set a direction on a folder
node and it applies to every tool inside *where it makes sense*, leaving the
rest untouched.
"""
import pytest

from freecad.Loobric.viewmodel import (
    cascade_choice, SKIP, LOCAL_WINS, SERVER_WINS,
    field_value, field_source, record_name, instance_shape, instance_diameter,
    short_id, fmt_dia)


# -- cascade -----------------------------------------------------------------

@pytest.mark.unit
def test_skip_always_skips():
    assert cascade_choice(SKIP, has_local=True, has_server=True,
                          is_deletion=False) == SKIP
    assert cascade_choice(SKIP, has_local=False, has_server=False,
                          is_deletion=True) == SKIP


@pytest.mark.unit
def test_local_wins_needs_a_local_file():
    # a normal row with a local file accepts 'upload'
    assert cascade_choice(LOCAL_WINS, True, True, False) == LOCAL_WINS
    # a server-only row (new_server) has nothing to upload -> untouched
    assert cascade_choice(LOCAL_WINS, has_local=False, has_server=True,
                          is_deletion=False) is None


@pytest.mark.unit
def test_server_wins_needs_a_server_record():
    assert cascade_choice(SERVER_WINS, True, True, False) == SERVER_WINS
    # a local-only row (new_local) has nothing to download -> untouched
    assert cascade_choice(SERVER_WINS, has_local=True, has_server=False,
                          is_deletion=False) is None


@pytest.mark.unit
def test_deletion_rows_accept_either_direction():
    # deleted_local: has a server record, no local file; index1 = propagate,
    # index2 = restore — both are valid positional choices.
    assert cascade_choice(LOCAL_WINS, has_local=False, has_server=True,
                          is_deletion=True) == LOCAL_WINS
    assert cascade_choice(SERVER_WINS, has_local=False, has_server=True,
                          is_deletion=True) == SERVER_WINS
    # deleted_server: local file, no server record — still both valid.
    assert cascade_choice(LOCAL_WINS, has_local=True, has_server=False,
                          is_deletion=True) == LOCAL_WINS
    assert cascade_choice(SERVER_WINS, has_local=True, has_server=False,
                          is_deletion=True) == SERVER_WINS


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
