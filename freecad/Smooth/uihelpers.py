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
# Tool-set ↔ machine coverage (pure: turns the /coverage JSON into rows)
# ---------------------------------------------------------------------------

# Severity bands, worst first. The coverage view colors and sorts by these so
# the tools that need physical action float to the top.
SEV_CRITICAL, SEV_WARNING, SEV_INFO, SEV_OK = (
    "critical", "warning", "info", "ok")

# How each coverage status presents. ``severity`` drives sort order and color;
# ``label`` is the column text; ``hint`` explains what to do about it. The star
# case is ``absent_on_machine`` — a tool in the CAM library not yet on the
# machine, i.e. the one to order/load — which is why it is CRITICAL.
COVERAGE_STATUS = {
    "in_sync": {
        "severity": SEV_OK, "label": "on machine",
        "hint": "This tool is loaded on the machine at the same T-number."},
    "number_mismatch": {
        "severity": SEV_WARNING, "label": "T-number differs",
        "hint": "On the machine, but at a different tool number than the set."},
    "absent_on_machine": {
        "severity": SEV_CRITICAL, "label": "NOT on machine",
        "hint": "In the CAM library but not on the machine — order or load it."},
    "machine_only": {
        "severity": SEV_INFO, "label": "machine only",
        "hint": "Loaded on the machine but not part of this set."},
    "unbound_slot": {
        "severity": SEV_INFO, "label": "empty slot",
        "hint": "An empty machine tool-table slot (no tool bound)."},
}
_COLLISION = {
    "severity": SEV_CRITICAL, "label": "T-number collision",
    "hint": "Two tools claim the same machine tool number — fix the numbering."}
_UNKNOWN = {"severity": SEV_INFO, "label": "?", "hint": ""}

# Sort key: worst severity first, then by T-number (None last), then label.
_SEV_ORDER = {SEV_CRITICAL: 0, SEV_WARNING: 1, SEV_INFO: 2, SEV_OK: 3}


def coverage_status_info(status, collides=False):
    """Presentation metadata (severity/label/hint) for one coverage status.

    A collision outranks the member's own status (a colliding tool is critical
    regardless of whether its number matches), so ``collides=True`` returns the
    collision band. Unknown statuses degrade to a neutral INFO row rather than
    raising — the view must never crash on a status the server adds later."""
    if collides:
        return dict(_COLLISION)
    return dict(COVERAGE_STATUS.get(status, _UNKNOWN))


def coverage_is_applicable(coverage):
    """True when the set is linked to a machine (coverage was computed)."""
    return bool((coverage or {}).get("applicable"))


def coverage_rows(coverage, names=None):
    """Flatten a ``/coverage`` report into display rows, worst first.

    ``coverage`` is the server payload; ``names`` optionally maps
    ``tool_record_id`` / ``bound_instance_id`` to a human tool name. Returns a
    list of row dicts::

        {tnum, name, status, severity, label, hint, collides, collides_with}

    Members come first conceptually but the list is sorted by severity so the
    ``absent_on_machine`` (and any collision) rows lead. Machine-only and empty
    slots are included as INFO rows. An inapplicable (unlinked) coverage yields
    an empty list — callers check :func:`coverage_is_applicable` first."""
    names = names or {}
    if not coverage_is_applicable(coverage):
        return []
    rows = []
    for member in coverage.get("members") or []:
        collides = bool(member.get("collides"))
        info = coverage_status_info(member.get("status"), collides=collides)
        tnum = member.get("set_number")
        rid = member.get("tool_record_id")
        rows.append({
            "tnum": tnum,
            "machine_tnum": member.get("machine_tool_number"),
            "name": names.get(rid) or (short_id({"internal": {"id": rid}})
                                       if rid else "?"),
            "status": member.get("status"),
            "severity": info["severity"],
            "label": info["label"],
            "hint": info["hint"],
            "collides": collides,
            "collides_with": list(member.get("collides_with") or []),
            "is_slot": False,
        })
    for slot in coverage.get("slots") or []:
        info = coverage_status_info(slot.get("status"))
        bound = slot.get("bound_instance_id")
        rows.append({
            "tnum": slot.get("tool_number"),
            "machine_tnum": slot.get("tool_number"),
            "name": (names.get(bound) or (short_id({"internal": {"id": bound}})
                                          if bound else "(empty)")),
            "status": slot.get("status"),
            "severity": info["severity"],
            "label": info["label"],
            "hint": info["hint"],
            "collides": False,
            "collides_with": [],
            "is_slot": True,
        })
    rows.sort(key=lambda r: (_SEV_ORDER.get(r["severity"], 9),
                             _tnum_sort_key(r["tnum"]), r["name"]))
    return rows


def _tnum_sort_key(tnum):
    """Sort tool numbers numerically; push missing ones to the end."""
    return (1, 0) if tnum is None else (0, tnum)


def coverage_summary_line(coverage):
    """A one-line human summary of a coverage report (linked or not).

    For a linked set, leads with the count that matters most — how many set
    tools are NOT yet on the machine — then the other notable counts. For an
    unlinked set, returns the server's reason."""
    if not coverage_is_applicable(coverage):
        reason = (coverage or {}).get("reason")
        return reason or "This set isn't linked to a machine."
    s = coverage.get("summary") or {}
    parts = []
    absent = s.get("absent_on_machine")
    if absent:
        parts.append("%d not on machine" % absent)
    if s.get("number_collision"):
        parts.append("%d collision(s)" % s["number_collision"])
    if s.get("number_mismatch"):
        parts.append("%d T-number mismatch(es)" % s["number_mismatch"])
    if s.get("in_sync"):
        parts.append("%d in sync" % s["in_sync"])
    extra = []
    if s.get("machine_only"):
        extra.append("%d machine-only" % s["machine_only"])
    if s.get("unbound_slot"):
        extra.append("%d empty slot(s)" % s["unbound_slot"])
    head = "; ".join(parts) if parts else "no set members"
    if extra:
        head += " (" + ", ".join(extra) + ")"
    return head


# ---------------------------------------------------------------------------
# Status model: Existence × Sync, kept deliberately separate (issue #9)
# ---------------------------------------------------------------------------
#
# Two orthogonal axes describe any tool object reached through the sync plan:
#
# - EXISTENCE — where the object lives right now: only in the FreeCAD library
#   (Local Only), only on the server (Server Only), or in both (Both).
# - SYNC — the relationship between the two copies: Synced (identical), Modified
#   (one side moved, a direction is obvious), Conflict (both moved — and with no
#   stored base we cannot pick a side), Pending (a create/delete waiting to be
#   propagated), or Error.
#
# They are derived from the single ``action`` that ``sync.plan_sync`` already
# computes, so the GUI never re-classifies — it only reads these.

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
    """True when an item needs attention — anything that is not in sync."""
    return sync_status_of(action) != SYNC_SYNCED


# ---------------------------------------------------------------------------
# Needs Attention — CAM content axis (per-object Local/Server/Modified)
# ---------------------------------------------------------------------------

# How each content exception presents in the Needs-Attention stream. ``severity``
# drives sort order and color (shared with the coverage bands); ``direction`` is
# the one-tap resolution this exception suggests, or None when the user must
# choose a side (a conflict or a deletion).
CONTENT_STATUS = {
    "conflict": {
        "severity": SEV_CRITICAL, "label": "changed on both sides",
        "direction": None,
        "hint": "Edited locally AND on the server — choose which copy to keep."},
    "push": {
        "severity": SEV_WARNING, "label": "modified here",
        "direction": "push",
        "hint": "Changed in this FreeCAD library; upload to update the server."},
    "pull": {
        "severity": SEV_WARNING, "label": "modified on server",
        "direction": "pull",
        "hint": "Changed on the server; download to update this library."},
    "deleted_local": {
        "severity": SEV_WARNING, "label": "deleted here",
        "direction": None,
        "hint": "The file was deleted here — propagate the deletion or restore it."},
    "deleted_server": {
        "severity": SEV_WARNING, "label": "deleted on server",
        "direction": None,
        "hint": "The record was deleted on the server — re-upload or delete the file."},
    "new_local": {
        "severity": SEV_INFO, "label": "local only",
        "direction": "push",
        "hint": "In this library but not on the server yet — upload it."},
    "new_server": {
        "severity": SEV_INFO, "label": "server only",
        "direction": "pull",
        "hint": "On the server but not in this library — download it."},
}


def content_status_info(action):
    """Presentation metadata (severity/label/direction/hint) for one content
    exception. Unknown actions degrade to a neutral INFO row."""
    info = CONTENT_STATUS.get(action)
    if info is None:
        return {"severity": SEV_INFO, "label": str(action),
                "direction": None, "hint": ""}
    return dict(info)


def content_attention_rows(plan_items):
    """The CAM-content (library axis) Needs-Attention rows.

    Folds ``sync.plan_sync`` items down to only the ones that need attention —
    dropping every in-sync object so healthy tools stay quiet — and tags each
    with its existence/sync bands and presentation. One row per object (the plan
    already yields one item per toolbit/library regardless of how many libraries
    reference it), so the *same* underlying object resolves once: 'one conflict,
    one resolution'."""
    rows = []
    for it in plan_items or []:
        action = it.get("action")
        if not is_exception(action):
            continue
        info = content_status_info(action)
        rows.append({
            "axis": "content",
            "key": it.get("key"),
            "kind": it.get("kind"),
            "name": it.get("name") or "?",
            "group": it.get("group"),
            "existence": existence_of(action),
            "sync_status": sync_status_of(action),
            "action": action,
            "direction": info["direction"],
            "severity": info["severity"],
            "label": info["label"],
            "hint": info["hint"],
            "detail": it.get("detail", ""),
            "record": it.get("record"),
            "diff": it.get("diff") or [],
        })
    return rows


# ---------------------------------------------------------------------------
# Needs Attention — machine-coverage axis (folds the old Coverage tab in)
# ---------------------------------------------------------------------------

def coverage_attention_rows(coverage, set_name=None, set_id=None, names=None):
    """The machine-coverage (control axis) Needs-Attention rows for ONE set.

    Reuses :func:`coverage_rows` and keeps only the rows that warrant action —
    ``absent_on_machine`` (a library tool not yet on the machine — order/load
    it), ``number_mismatch``, and collisions — dropping the in-sync members and
    the purely-informational machine-only / empty-slot rows. Each row is tagged
    with the owning set so the aggregated view can say where it came from. An
    unlinked set contributes nothing (callers surface those separately)."""
    out = []
    for row in coverage_rows(coverage, names=names):
        if row["severity"] in (SEV_OK, SEV_INFO):
            continue
        tagged = dict(row)
        tagged["axis"] = "coverage"
        tagged["set_name"] = set_name
        tagged["set_id"] = set_id
        out.append(tagged)
    return out


def needs_attention_rows(content=None, coverage=None):
    """Aggregate the two exception axes into one stream, worst first.

    ``content`` comes from :func:`content_attention_rows` (the CAM library axis)
    and ``coverage`` is the concatenation of :func:`coverage_attention_rows` over
    every machine-linked set (the control axis). The merged list is sorted by
    severity so collisions and ``absent_on_machine``/conflict rows lead; within a
    severity, coverage rows sort before content and then by name. An empty result
    means everything is healthy."""
    rows = list(content or []) + list(coverage or [])
    rows.sort(key=lambda r: (_SEV_ORDER.get(r.get("severity"), 9),
                             0 if r.get("axis") == "coverage" else 1,
                             str(r.get("name") or "")))
    return rows


# ---------------------------------------------------------------------------
# Libraries view — derived per-library rollup
# ---------------------------------------------------------------------------

def library_rollup(items):
    """Summarize a library's CONTENTS (its bit items) into counts.

    A library is organization, not a sync object, so it is never itself
    'conflicted' — it merely *summarizes* what it holds. Returns counts keyed
    ``synced / local_only / server_only / modified / conflict / deleted / total``.
    Each item is placed in exactly one bucket; the order of checks keeps the
    buckets disjoint (synced, then conflict, then deletions, then existence-only
    creates, then modified)."""
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
    """Render :func:`library_rollup` counts as the library's one-line rollup:
    ``✓ N synced · ↑ N local-only · ↓ N server-only · ⚠ N conflicts`` — with
    ``✎ N modified`` and ``✖ N deleted`` appended only when non-zero (so the
    common case stays terse)."""
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


# ---------------------------------------------------------------------------
# Per-object resolution — side-by-side field comparison
# ---------------------------------------------------------------------------

# Known .fctb flat field -> canonical path, so the server side of a resolution
# row can be annotated with the canonical Field's provenance (who asserted it).
# Best-effort: a field with no mapping simply carries no source.
_FCTB_TO_CANONICAL = {
    "name": ("name",),
    "shape": ("geometry", "shape"),
    "shape-type": ("geometry", "shape"),
    "parameter.Diameter": ("geometry", "diameter"),
}


def field_provenance(record, fctb_field):
    """Best-effort canonical provenance string (e.g. ``asserted:human@web``) for
    a .fctb field's server value, or None when the field has no canonical mapping
    or no recorded source. Used to label the server column in the resolution
    view per the schema's field provenance."""
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
    ``{field, local, server, changed_by, server_source}`` where ``server_source``
    is the canonical provenance of the server value where known."""
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
# object untouched; the two 'keep' choices reuse the existing apply write paths
# (push uploads the local copy, pull downloads the server copy).
RESOLUTION_DECISION = {"keep_local": "push", "keep_server": "pull",
                       "skip": "skip"}


def resolution_actions(item):
    """The resolution choices offered for one content exception, as
    ``[(choice_key, label)]``. Keep Local / Keep Server reuse the existing
    upload/download write paths; Skip is always available. (Coverage-axis
    exceptions are physical/operator-lane and are NOT resolved here — see the
    Needs-Attention tab.)"""
    return [("keep_local", "Keep Local (upload)"),
            ("keep_server", "Keep Server (download)"),
            ("skip", "Skip")]


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
