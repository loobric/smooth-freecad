# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
The Smooth GUI **view-model** — pure functions that answer "what should the
window show", computed once and rendered by the widgets in SmoothTabs.py. **No
FreeCAD or PySide imports**, so it is unit-testable headless.

Three groups:
- Provenance-tagged canonical readers: a sectioned record's canonical leaf is
  ``{value, source}`` (or, leniently, a bare value); these read display values
  out without the caller reaching into the structure.
- View builders, one per primary surface — :func:`sync_tree` (the Sync tab's
  grouped plan) and :func:`machine_tables` (the Machines tab's per-machine tool
  tables with pending bindings folded in). The widgets only render their output.
- The sync-row decision helpers: the Existence/Sync status axes, the per-object
  resolution rows, and the folder-node ``cascade_choice``.
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


# The honest-sparse marker: a field we don't have renders as this, never as a
# blank cell or a fabricated zero (the provenance discipline, made visible).
MISSING = "—"


# ---------------------------------------------------------------------------
# Catalog tab — the browse table (one row per ToolCatalogRecord)
# ---------------------------------------------------------------------------

def _catalog_text(field):
    """Display a catalog Field's value, or the honest-sparse marker when it is
    absent — never a blank, never a coerced 0."""
    value = field_value(field)
    return MISSING if value in (None, "") else str(value)


def _catalog_geometry(record):
    """A short geometry summary for a catalog row: the nominal diameter (with
    unit when present), else the shape, else the honest-sparse marker."""
    dia = canonical(record, "geometry", "diameter")
    value = field_value(dia)
    if value is not None:
        unit = dia.get("unit") if isinstance(dia, dict) else None
        return "⌀%s%s" % (value, unit or "")
    shape = field_value(canonical(record, "geometry", "shape"))
    return str(shape) if shape else MISSING


def catalog_rows(catalog_records):
    """Render model for the Catalog browse table — one row per catalog record.

    Each row is ``{id, name, manufacturer, product_code, geometry, source}``:
    ``geometry`` is the nominal-diameter summary and ``source`` is the provenance
    of the name (falling back to the diameter's), so where a catalog's facts came
    from stays visible — mirroring the web. Honest-sparse: a missing field is the
    :data:`MISSING` marker, never a blank or a fabricated zero. Pure: the widget
    only renders this."""
    rows = []
    for record in catalog_records or []:
        name_field = canonical(record, "name")
        dia_field = canonical(record, "geometry", "diameter")
        rows.append({
            "id": (record.get("internal") or {}).get("id"),
            "name": _catalog_text(name_field),
            "manufacturer": _catalog_text(canonical(record, "manufacturer")),
            "product_code": _catalog_text(canonical(record, "product_code")),
            "geometry": _catalog_geometry(record),
            "source": field_source(name_field) or field_source(dia_field)
                      or MISSING,
        })
    return rows


# ---------------------------------------------------------------------------
# Status model: Existence × Sync, kept deliberately separate
# ---------------------------------------------------------------------------
#
# Two orthogonal axes describe any tool object reached through the sync plan:
#
# - EXISTENCE — where the object lives right now: only in the FreeCAD library
#   (Local Only), only on the server (Server Only), or in both (Both).
# - SYNC — the relationship between the two copies: Synced (identical), Modified
#   (one side moved), Conflict (both moved), Pending (a create/delete waiting to
#   propagate), or Error.
#
# They are derived from the single ``action`` that ``sync.plan_sync`` computes,
# so the GUI never re-classifies — it only reads these.

EXIST_LOCAL_ONLY, EXIST_SERVER_ONLY, EXIST_BOTH = (
    "local_only", "server_only", "both")
SYNC_SYNCED, SYNC_MODIFIED, SYNC_CONFLICT, SYNC_PENDING, SYNC_ERROR = (
    "synced", "modified", "conflict", "pending", "error")

_ACTION_EXISTENCE = {
    "unchanged": EXIST_BOTH,
    "push": EXIST_BOTH,
    "pull": EXIST_BOTH,
    "conflict": EXIST_BOTH,
    "new_local": EXIST_LOCAL_ONLY,
    "deleted_server": EXIST_LOCAL_ONLY,   # file is still here; record is gone
    "new_server": EXIST_SERVER_ONLY,
    "deleted_local": EXIST_SERVER_ONLY,   # record is still there; file is gone
}
_ACTION_SYNC = {
    "unchanged": SYNC_SYNCED,
    "push": SYNC_MODIFIED,
    "pull": SYNC_MODIFIED,
    "conflict": SYNC_CONFLICT,
    "new_local": SYNC_PENDING,
    "new_server": SYNC_PENDING,
    "deleted_local": SYNC_PENDING,
    "deleted_server": SYNC_PENDING,
}


def existence_of(action):
    """Existence band {Both, Local Only, Server Only} for a plan action.
    Unknown actions degrade to Both rather than raising."""
    return _ACTION_EXISTENCE.get(action, EXIST_BOTH)


def sync_status_of(action):
    """Sync band {Synced, Modified, Conflict, Pending, Error} for a plan action.
    An action the planner adds later degrades to Error (visible, never silent)."""
    return _ACTION_SYNC.get(action, SYNC_ERROR)


def is_exception(action):
    """True when an item needs sync — anything that is not already in sync."""
    return sync_status_of(action) != SYNC_SYNCED


# How each sync status presents in a row. ``direction`` is the one-tap default
# this status suggests, or None when the user must choose a side (conflict /
# deletion).
SYNC_STATUS = {
    "conflict": {"label": "changed on both sides", "direction": None,
                 "hint": "Edited locally AND on the server — choose which copy to keep."},
    "push": {"label": "changed here", "direction": "push",
             "hint": "Changed in this FreeCAD library; upload to update the server."},
    "pull": {"label": "changed on server", "direction": "pull",
             "hint": "Changed on the server; download to update this library."},
    "deleted_local": {"label": "deleted here", "direction": None,
                      "hint": "The file was deleted here — propagate the deletion or restore it."},
    "deleted_server": {"label": "deleted on server", "direction": None,
                       "hint": "The record was deleted on the server — re-upload or delete the file."},
    "new_local": {"label": "new here", "direction": "push",
                  "hint": "In this library but not on the server yet — upload it."},
    "new_server": {"label": "server only", "direction": "pull",
                   "hint": "On the server but not in this library — download it."},
    "unchanged": {"label": "in sync", "direction": None, "hint": "In sync."},
}


def row_status_info(action):
    """Presentation metadata (label/direction/hint) for one plan action.
    Unknown actions degrade to a neutral row rather than raising."""
    info = SYNC_STATUS.get(action)
    if info is None:
        return {"label": str(action), "direction": None, "hint": ""}
    return dict(info)


# ---------------------------------------------------------------------------
# Sync tab — the grouped plan (one ToolSet node per .fctl, its tools beneath)
# ---------------------------------------------------------------------------

def library_rollup(items):
    """Summarize a ToolSet's CONTENTS (its tool items) into counts.

    A ToolSet is organization, not a sync object, so it is never itself
    'conflicted' — it merely *summarizes* what it holds. Returns counts keyed
    ``synced / local_only / server_only / modified / conflict / deleted / total``.
    Each item lands in exactly one bucket; the order of checks keeps them
    disjoint (synced, conflict, deletions, existence-only creates, modified)."""
    counts = {"synced": 0, "local_only": 0, "server_only": 0,
              "modified": 0, "conflict": 0, "deleted": 0, "total": 0}
    for it in items or []:
        action = it.get("action")
        counts["total"] += 1
        sync_status = sync_status_of(action)
        existence = existence_of(action)
        if sync_status == SYNC_SYNCED:
            counts["synced"] += 1
        elif sync_status == SYNC_CONFLICT:
            counts["conflict"] += 1
        elif action in ("deleted_local", "deleted_server"):
            counts["deleted"] += 1
        elif existence == EXIST_LOCAL_ONLY:
            counts["local_only"] += 1
        elif existence == EXIST_SERVER_ONLY:
            counts["server_only"] += 1
        elif sync_status == SYNC_MODIFIED:
            counts["modified"] += 1
    return counts


def library_rollup_line(counts):
    """Render :func:`library_rollup` counts as a ToolSet's one-line rollup:
    ``✓ N synced · ↑ N local-only · ↓ N server-only · ⚠ N conflicts`` — with
    ``✎ N modified`` and ``✖ N deleted`` appended only when non-zero."""
    counts = counts or {}
    parts = [
        "✓ %d synced" % counts.get("synced", 0),
        "↑ %d local-only" % counts.get("local_only", 0),
        "↓ %d server-only" % counts.get("server_only", 0),
        "⚠ %d conflicts" % counts.get("conflict", 0),
    ]
    if counts.get("modified"):
        parts.append("✎ %d modified" % counts["modified"])
    if counts.get("deleted"):
        parts.append("✖ %d deleted" % counts["deleted"])
    return " · ".join(parts)


def sync_tree(plan_items, attention_only=False):
    """The Sync tab's render model: the plan grouped into ToolSet nodes, each
    with its derived rollup line and the tool rows beneath it.

    Returns ``{"groups": [...], "pending": int}`` where each group is::

        {"kind": "library"|"loose",
         "name": str,
         "rollup": str,                 # the derived one-line summary
         "library_item": item|None,     # the ToolSet's own plan item (for its row)
         "members": [item, ...]}        # the tool items beneath it

    Grouping mirrors what the dialog used to compute inline: a tool sits under
    its owning ToolSet whether that set lives locally or only on the server
    (``item['group']``); tools in no set fall into a 'loose' group. ``pending``
    counts items that need sync (the apply button enables on it).

    ``attention_only`` filters to just the items needing sync — dropping in-sync
    tools and any group left with neither an actionable ToolSet row nor members
    (so 'needs attention' is a view over this one plan, never a second plan)."""
    items = plan_items or []
    libraries = [i for i in items if i.get("kind") == "library"]
    bits = [i for i in items if i.get("kind") == "bit"]

    bits_by_group = {}
    for bit in bits:
        bits_by_group.setdefault(bit.get("group"), []).append(bit)

    def _keep(item):
        return (not attention_only) or is_exception(item.get("action"))

    groups = []
    shown = set()
    for library in sorted(libraries, key=lambda i: i.get("name") or ""):
        members = sorted(bits_by_group.get(library.get("group"), []),
                         key=lambda i: i.get("name") or "")
        for bit in members:
            shown.add(bit.get("key"))
        rollup = library_rollup_line(library_rollup(members))
        lib_item = library if _keep(library) else None
        kept_members = [m for m in members if _keep(m)]
        if attention_only and lib_item is None and not kept_members:
            continue
        groups.append({
            "kind": "library", "name": library.get("name") or "?",
            "rollup": rollup, "library_item": library, "members": kept_members,
        })

    loose = sorted((b for b in bits if b.get("key") not in shown),
                   key=lambda i: i.get("name") or "")
    kept_loose = [b for b in loose if _keep(b)]
    if loose and (kept_loose or not attention_only):
        groups.append({
            "kind": "loose", "name": "Not in any tool set",
            "rollup": library_rollup_line(library_rollup(loose)),
            "library_item": None, "members": kept_loose,
        })

    pending = sum(1 for it in items if is_exception(it.get("action")))
    return {"groups": groups, "pending": pending}


# ---------------------------------------------------------------------------
# Machines tab — per-machine tool tables, with pending bindings folded in
# ---------------------------------------------------------------------------

def machine_tables(machines, entries, instances=None, proposals=None):
    """The Machines tab's render model: every machine with its tool-table
    entries, each entry annotated with its bound-tool name and any pending
    binding proposal from the Inbox (folded in — Machines is the one binding
    surface).

    Returns ``{"machines": [...], "pending": int}`` where each machine is::

        {"machine": record, "name", "controller", "id",
         "entries": [
            {"entry": record, "tool_number", "description", "diameter",
             "bound_instance_id", "bound_name"|None,
             "proposal": {"proposal_id", "name", "diameter", "confidence",
                          "reason"}|None},
            ...]}

    ``pending`` is the number of entries carrying a proposal (the 'Pending
    review' band counts it). Pure join: the widget only renders this."""
    names = {}
    for inst in instances or []:
        names[(inst.get("internal") or {}).get("id")] = record_name(inst)

    proposal_by_entry = {}
    for p in proposals or []:
        eid = (p.get("entry") or {}).get("id")
        if eid:
            proposal_by_entry[eid] = p

    entries_by_machine = {}
    for entry in entries or []:
        mid = (entry.get("internal") or {}).get("machine_id")
        entries_by_machine.setdefault(mid, []).append(entry)

    pending = 0
    out = []
    for machine in sorted(machines or [], key=record_name):
        mid = (machine.get("internal") or {}).get("id")
        rows = []
        for entry in sorted(
                entries_by_machine.get(mid, []),
                key=lambda e: field_value(canonical(e, "tool_number")) or 0):
            eid = (entry.get("internal") or {}).get("id")
            bound = field_value(canonical(entry, "bound_instance_id"))
            proposal = None
            if not bound:
                p = proposal_by_entry.get(eid)
                if p and p.get("id"):
                    inst = p.get("proposed_instance") or {}
                    proposal = {
                        "proposal_id": p["id"],
                        "name": inst.get("name"),
                        "diameter": inst.get("diameter"),
                        "confidence": p.get("confidence"),
                        "reason": p.get("reason"),
                    }
                    pending += 1
            rows.append({
                "entry": entry,
                "tool_number": field_value(canonical(entry, "tool_number")),
                "description": field_value(canonical(entry, "description")),
                "diameter": field_value(canonical(entry, "offsets", "diameter")),
                "bound_instance_id": bound,
                "bound_name": names.get(bound) if bound else None,
                "proposal": proposal,
            })
        out.append({
            "machine": machine,
            "name": record_name(machine),
            "controller": field_value(canonical(machine, "controller_type")),
            "id": mid,
            "entries": rows,
        })
    return {"machines": out, "pending": pending}


# ---------------------------------------------------------------------------
# Per-object resolution — side-by-side field comparison
# ---------------------------------------------------------------------------

# Known .fctb flat field -> canonical path, so the server side of a resolution
# row can be annotated with the canonical Field's provenance (who asserted it).
_FCTB_TO_CANONICAL = {
    "name": ("name",),
    "shape": ("geometry", "shape"),
    "shape-type": ("geometry", "shape"),
    "parameter.Diameter": ("geometry", "diameter"),
}


def field_provenance(record, fctb_field):
    """Best-effort canonical provenance string (e.g. ``asserted:human@web``) for
    a .fctb field's server value, or None when the field has no canonical mapping
    or no recorded source."""
    if not record:
        return None
    path = _FCTB_TO_CANONICAL.get(fctb_field)
    if not path:
        return None
    return field_source(canonical(record, *path))


def resolution_rows(item):
    """The per-field side-by-side for one object's resolution view.

    Built from the item's already-computed ``diff`` (sync.diff_docs) so the
    comparison logic is not reinvented; each row is
    ``{field, local, server, changed_by, server_source}``."""
    record = item.get("record")
    out = []
    for d in item.get("diff") or []:
        field = d.get("field")
        out.append({
            "field": field,
            "local": d.get("local"),
            "server": d.get("server"),
            "changed_by": d.get("changed_by"),
            "server_source": field_provenance(record, field),
        })
    return out


# Resolution choice -> the sync apply DIRECTION it maps onto. 'skip' leaves the
# object untouched; the two 'keep' choices reuse the existing apply write paths.
RESOLUTION_DECISION = {"keep_local": "push", "keep_server": "pull", "skip": "skip"}


def resolution_actions(item):
    """The resolution choices offered for one sync exception, as
    ``[(choice_key, label)]``. Keep Local / Keep Server reuse the existing
    upload/download write paths; Skip is always available."""
    return [("keep_local", "Keep Local (upload)"),
            ("keep_server", "Keep Server (download)"),
            ("skip", "Skip")]


# ---------------------------------------------------------------------------
# Bulk-action cascade (the folder-node "set all" menu)
# ---------------------------------------------------------------------------

# Combo index convention shared by every sync row:
#   0 = leave unsynced (skip)
#   1 = local wins  (upload / propagate-deletion / restore-upload)
#   2 = server wins (download / restore-file / delete-local)
SKIP, LOCAL_WINS, SERVER_WINS = 0, 1, 2


def cascade_choice(node_index, has_local, has_server, is_deletion):
    """Map a folder-node direction onto one child row.

    ``node_index`` is the direction the user set on the ToolSet/group node
    (SKIP / LOCAL_WINS / SERVER_WINS). Returns the combo index to apply to a
    child with the given capabilities, or ``None`` to leave the child untouched
    (the direction doesn't apply — e.g. 'download' on a tool that exists only
    locally). Deletion rows always accept index 1/2 (their positional choices
    are 'propagate'/'restore'), so a real direction never skips them."""
    if node_index == SKIP:
        return SKIP
    if node_index == LOCAL_WINS:
        return LOCAL_WINS if (is_deletion or has_local) else None
    if node_index == SERVER_WINS:
        return SERVER_WINS if (is_deletion or has_server) else None
    return None
