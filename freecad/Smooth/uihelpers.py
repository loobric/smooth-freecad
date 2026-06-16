# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Pure helpers for the Smooth GUI — **no FreeCAD or PySide imports**, so they are
unit-testable headless. SmoothTabs.py imports everything here.

Two groups:
- Provenance-tagged canonical readers: a sectioned record's canonical leaf is
  ``{value, source}`` (or, leniently, a bare value); these read display values
  out without the caller reaching into the structure.
- The sync-tab bulk-action ``cascade_choice``: how a folder-node direction maps
  onto one child row.
"""
from . import mapping


# ---------------------------------------------------------------------------
# Provenance-tagged canonical readers
# ---------------------------------------------------------------------------

def field_value(field):
    return field.get("value") if isinstance(field, dict) else field


def field_source(field):
    return field.get("source") if isinstance(field, dict) else None


def canonical(record, *path):
    """Walk into ``record['canonical']`` along ``path``; returns the leaf
    (still a Field/value) or None."""
    node = record.get("canonical") or {}
    for part in path[:-1]:
        node = node.get(part) or {}
    return node.get(path[-1]) if path else node


def short_id(record):
    rid = (record.get("internal") or {}).get("id") or ""
    return rid[:8] if rid else "?"


def record_name(record):
    """A human label for any sectioned record: canonical name, then the
    FreeCAD label/client_item_id, then the short id."""
    name = field_value(canonical(record, "name"))
    if name:
        return name
    section = (record.get("clients") or {}).get(mapping.CLIENT_NAME) or {}
    data = section.get("data") or {}
    return data.get("fctl_label") or section.get("client_item_id") or short_id(record)


def instance_shape(record):
    return field_value(canonical(record, "geometry", "shape"))


def instance_diameter(record):
    return field_value(canonical(record, "geometry", "diameter"))


def fmt_dia(value):
    return "%.3g mm" % value if isinstance(value, (int, float)) else "—"


# ---------------------------------------------------------------------------
# Bulk-action cascade
# ---------------------------------------------------------------------------

# Combo index convention shared by every sync row:
#   0 = leave unsynced (skip)
#   1 = local wins  (upload / propagate-deletion / restore-upload)
#   2 = server wins (download / restore-file / delete-local)
SKIP, LOCAL_WINS, SERVER_WINS = 0, 1, 2


def cascade_choice(node_index, has_local, has_server, is_deletion):
    """Map a folder-node direction onto one child row.

    ``node_index`` is the direction the user set on the library/group node
    (SKIP / LOCAL_WINS / SERVER_WINS). Returns the combo index to apply to a
    child with the given capabilities, or ``None`` to leave the child untouched
    (the direction doesn't apply — e.g. 'download' on a tool that exists only
    locally). Deletion rows always accept index 1/2 (their positional choices
    are 'propagate'/'restore'), so a real direction never skips them.
    """
    if node_index == SKIP:
        return SKIP
    if node_index == LOCAL_WINS:
        return LOCAL_WINS if (is_deletion or has_local) else None
    if node_index == SERVER_WINS:
        return SERVER_WINS if (is_deletion or has_server) else None
    return None
