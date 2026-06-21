# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Plan / apply sync orchestration — reconcile FreeCAD tool dirs with a Smooth
server speaking the **sectioned** tool schema (docs/TOOL_SCHEMA.md).

Pure logic over the filesystem and an injected client (``SmoothApi``); no
FreeCAD imports, fully testable headless. The dialog (SmoothDialog.py) renders the
plan this module computes and feeds back the per-row decisions.

The model in one line: a ``.fctb`` is a **ToolInstanceRecord**, a ``.fctl`` is a
**ToolSet**, each ``{internal, canonical, clients}``. FreeCAD owns its
``clients.freecad`` section (the lossless document) and *asserts* the canonical
facts its scope permits (name, geometry.shape, dimensions, a set's name and
members). The server's ``internal.id`` is persisted client-side as the additive
``smooth.record_id`` key — the same identity mechanism as before, now uniform
across bits and libraries.

Identity rules (the production lesson — never create duplicates):
- A bit/library exported before carries its server id in the additive
  ``smooth`` key; re-sync writes that id's sections, never blind-creates.
- After a create the id is written back immediately, so even a crashed sync
  never double-creates.
- If an editor dropped the ``smooth`` key (FreeCAD's ToolBit/library editors
  drop unknown top-level keys on save), re-adopt: bits by the verbatim ``.fctb``
  id (held server-side as ``client_item_id``), then by filename; libraries by
  the sync journal, then by recorded ``client_item_id``. NEVER by name.
- Bits sync before libraries: a set's membership needs record ids.
"""
import json
import os

from . import mapping
from .client import SmoothError


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, doc):
    """Write FreeCAD-style JSON (2-space indent, trailing newline)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _writeback_identity(path, server_id, version):
    """Inject/refresh the additive 'smooth' key in a tool file.

    Uniform across bits and libraries: both store the server id under
    ``smooth.record_id`` (the 2026-06 sectioned-schema unification)."""
    doc = _read_json(path)
    doc["smooth"] = {"record_id": server_id, "version": version}
    _write_json(path, doc)


def _sans_smooth(doc):
    """Copy of a tool document without the identity plumbing key."""
    return {k: v for k, v in (doc or {}).items() if k != "smooth"}


def _slug(name):
    """Filesystem-safe filename stem from a record name."""
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "tool"


# ---------------------------------------------------------------------------
# Sectioned-record accessors (the server returns {internal, canonical, clients})
# ---------------------------------------------------------------------------

def _record_id(record):
    return (record.get("internal") or {}).get("id")


def _record_version(record):
    return (record.get("internal") or {}).get("version")


def _freecad_section(record):
    return ((record.get("clients") or {}).get(mapping.CLIENT_NAME) or {})


def _client_item_id(record):
    return _freecad_section(record).get("client_item_id")


def _base_fctb(record):
    """The verbatim .fctb FreeCAD last pushed — the 3-way comparison base."""
    return (_freecad_section(record).get("data") or {}).get("fctb")


def _field_value(field):
    if isinstance(field, dict):
        return field.get("value")
    return field


def _record_name(record):
    """A human label for an instance OR set, from canonical name, then the
    FreeCAD label/client_item_id."""
    name = _field_value((record.get("canonical") or {}).get("name"))
    if name:
        return name
    data = _freecad_section(record).get("data") or {}
    return data.get("fctl_label") or _client_item_id(record)


def record_shape(record):
    """The canonical geometry.shape of an instance record (lowercased), or None.

    Provided so callers (and the dialog) read a bit's stored type without
    reaching into the sectioned canonical Fields themselves."""
    geometry = (record.get("canonical") or {}).get("geometry") or {}
    value = _field_value(geometry.get("shape"))
    return str(value).lower() if value else None


def _set_members(record):
    """Canonical member ``[{tool_record_id, number}]`` of a ToolSet, numbers
    unwrapped from their provenance Fields."""
    out = []
    for member in (record.get("canonical") or {}).get("members", []) or []:
        out.append({"tool_record_id": member.get("tool_record_id"),
                    "number": _field_value(member.get("number"))})
    return out


def scan_tools_dir(tools_dir):
    """Find bit and library files under a FreeCAD Tools directory.

    Returns (bit_paths, library_paths), sorted for determinism.

    Assumptions:
    - Standard layout: <tools_dir>/Bit/*.fctb, <tools_dir>/Library/*.fctl
    - Missing subdirectories mean empty lists, not errors
    """
    bit_dir = os.path.join(tools_dir, "Bit")
    lib_dir = os.path.join(tools_dir, "Library")
    bits = sorted(
        os.path.join(bit_dir, n) for n in os.listdir(bit_dir)
        if n.endswith(".fctb")
    ) if os.path.isdir(bit_dir) else []
    libs = sorted(
        os.path.join(lib_dir, n) for n in os.listdir(lib_dir)
        if n.endswith(".fctl")
    ) if os.path.isdir(lib_dir) else []
    return bits, libs


# ---------------------------------------------------------------------------
# 3-way classification + field diffs (semantic, formatting-insensitive)
# ---------------------------------------------------------------------------

def _semantic(doc):
    """Canonical flat form of a document for semantic equality."""
    return {k: _canonical_value(v) for k, v in _flatten(doc).items()}


def _classify(local_doc, base, regenerated):
    """3-way classification of one file (bit OR library).

    Comparison is SEMANTIC: quantity formatting churn from FreeCAD's editor
    ('6.0000 mm' -> '6.00 mm') does not count as change.

    Returns one of: "unchanged", "pull" (server changed), "push" (local
    changed), "conflict" (both changed). With no base (never synced through
    this install) only equality is trusted: equal -> unchanged, else conflict
    (never a guessed direction).
    """
    local_cmp = _semantic(local_doc)
    regen_cmp = _semantic(regenerated)
    if local_cmp == regen_cmp:
        return "unchanged"
    if base is not None and local_cmp == _semantic(base):
        return "pull"
    if base is not None and regen_cmp == _semantic(base):
        return "push"
    return "conflict"


def _canonical_value(value):
    """Normalize a leaf for SEMANTIC comparison: quantity strings compare
    by (value, unit) so '6.00 mm' == '6.0000 mm' (FreeCAD's ToolBit editor
    reformats quantities on every save - field finding 2026-06-11)."""
    if isinstance(value, str):
        number, unit = mapping.parse_quantity(value)
        if number is not None:
            return (number, unit)
    return value


def _flatten(doc, prefix=""):
    """Dotted-key flattening for field-level diffs ('smooth' excluded)."""
    out = {}
    for k, v in (doc or {}).items():
        if k == "smooth":
            continue
        key = prefix + k
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def diff_docs(local, base, regenerated):
    """Field-level differences between the local file and the server's
    version, with each difference attributed to the side that moved away
    from the last-synced base.

    Returns [{"field", "local", "server", "changed_by": local|server|both}].
    """
    lf, bf, rf = _flatten(local), _flatten(base or {}), _flatten(regenerated)
    diffs = []
    for field in sorted(set(lf) | set(rf)):
        lv, rv = lf.get(field), rf.get(field)
        clv, crv = _canonical_value(lv), _canonical_value(rv)
        if clv == crv:
            continue  # formatting-only difference: not a change
        cbv = _canonical_value(bf.get(field))
        if clv != cbv and crv != cbv:
            changed_by = "both"
        elif clv != cbv:
            changed_by = "local"
        else:
            changed_by = "server"
        diffs.append({"field": field, "local": lv, "server": rv,
                      "changed_by": changed_by})
    return diffs


def _membership_delta(local_doc, regenerated):
    """Human-readable library membership difference."""
    local_paths = [t.get("path") for t in (local_doc.get("tools") or [])]
    server_paths = [t.get("path") for t in (regenerated.get("tools") or [])]
    only_server = [p for p in server_paths if p not in local_paths]
    only_local = [p for p in local_paths if p not in server_paths]
    parts = []
    if only_server:
        parts.append("members only on server: " + ", ".join(only_server))
    if only_local:
        parts.append("members only here: " + ", ".join(only_local))
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# The sync journal: this install's memory of what it has synced
# ---------------------------------------------------------------------------

STATE_BASENAME = ".smooth_state.json"


def _load_sync_state(tools_dir):
    """This install's memory of what it has synced - the only way to tell
    'deleted here' from 'new on the server' (and vice versa), and the base for
    a library's 3-way membership classification (a set's section deliberately
    does NOT carry its membership — that's promoted into canonical, §7.4 — so
    the last-synced .fctl snapshot lives here instead)."""
    try:
        with open(os.path.join(tools_dir, STATE_BASENAME)) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    state.setdefault("records", {})       # instance id -> basename
    state.setdefault("tool_sets", {})     # set id -> basename
    state.setdefault("set_snapshots", {})  # set id -> last-synced .fctl doc
    return state


def _save_sync_state(tools_dir, state):
    try:
        with open(os.path.join(tools_dir, STATE_BASENAME), "w") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass


def _readopt_tool_set(basename, server_sets_by_id, state):
    """Deterministic library re-adoption when the .fctl identity key is
    missing/stale (FreeCAD's library editor drops unknown keys on save, same as
    the ToolBit editor). Journal first, then the client_item_id the server
    recorded at export. NEVER by name."""
    for sid, recorded in (state.get("tool_sets") or {}).items():
        if recorded == basename and sid in server_sets_by_id:
            return sid
    matches = [sid for sid, s in server_sets_by_id.items()
               if _client_item_id(s) == basename]
    if len(matches) == 1:
        return matches[0]
    return None


# ---------------------------------------------------------------------------
# Plan (read-only): classify every local file and every server record
# ---------------------------------------------------------------------------

def plan_sync(tools_dir, client, log=lambda msg: None):
    """Compute the sync plan without touching anything.

    Returns {"items": [...], "errors": [...]}; each item:
        {"key": str,                # stable handle for apply decisions
         "kind": "bit"|"library",
         "name": str, "path": str|None, "basename": str|None,
         "action": "unchanged"|"push"|"pull"|"new_local"|"new_server"
                   |"conflict"|"deleted_local"|"deleted_server",
         "detail": str,
         "library": str|None,       # owning .fctl basename for bits (local)
         "group": str|None,         # stable grouping handle (owning library,
                                    # local OR server) — the dialog groups on
                                    # this; None means "not in any library"
         "record": dict|None,       # sectioned server object when one exists
         "diff": [...]}             # field-level diff for changed items

    `log` receives human-readable progress for the Report view / log pane.
    """
    items = []
    errors = []
    log("Planning: reading local tools and server state…")
    state = _load_sync_state(tools_dir)
    bit_paths, lib_paths = scan_tools_dir(tools_dir)
    server_records = client.list_instances()
    server_sets = client.list_sets()
    server_record_ids = {_record_id(r) for r in server_records}
    server_set_ids = {_record_id(s) for s in server_sets}

    by_record_id, by_fctb_id, by_filename = {}, {}, {}
    local_docs = {}
    for path in bit_paths:
        basename = os.path.basename(path)
        try:
            doc = _read_json(path)
        except (OSError, ValueError) as e:
            errors.append("%s: unreadable (%s)" % (basename, e))
            continue
        local_docs[path] = doc
        rid = mapping.fctb_record_id(doc)
        if rid:
            by_record_id[rid] = path
        if doc.get("id"):
            by_fctb_id.setdefault(doc["id"], path)
        by_filename[basename] = path

    # bit membership: basename -> owning library basename (first wins)
    member_of = {}
    local_lib_docs = {}
    for lpath in lib_paths:
        try:
            ldoc = _read_json(lpath)
        except (OSError, ValueError) as e:
            errors.append("%s: unreadable (%s)" % (os.path.basename(lpath), e))
            continue
        local_lib_docs[lpath] = ldoc
        for tool in ldoc.get("tools", []) or []:
            member_of.setdefault(tool.get("path"), os.path.basename(lpath))

    def _match_bit(record):
        cii = _client_item_id(record)
        return (by_record_id.get(_record_id(record))
                or (cii and by_fctb_id.get(cii))
                or (cii and by_filename.get(cii)))

    # record id -> the basename it is known by (local file, else recorded
    # client_item_id) — for library membership rendering
    name_by_record_id = {}
    for record in server_records:
        rid = _record_id(record)
        path = _match_bit(record)
        if path:
            name_by_record_id[rid] = os.path.basename(path)
        else:
            cii = _client_item_id(record)
            name_by_record_id[rid] = (state["records"].get(rid) or cii
                                      or (rid[:8] + ".fctb"))

    matched_paths = set()
    for record in server_records:
        rid = _record_id(record)
        base = _base_fctb(record)
        path = _match_bit(record)
        if not path or path not in local_docs:
            basename = state["records"].get(rid) or _client_item_id(record)
            if rid in state["records"]:
                items.append({
                    "key": "server:%s" % rid, "kind": "bit",
                    "name": _record_name(record) or "?", "path": None,
                    "basename": basename, "action": "deleted_local",
                    "detail": "the file '%s' was deleted here - propagate the "
                              "deletion to the server, or restore the file"
                              % (basename or "?"),
                    "library": None, "record": record, "diff": [],
                })
            else:
                items.append({
                    "key": "server:%s" % rid, "kind": "bit",
                    "name": _record_name(record) or "?", "path": None,
                    "basename": basename, "action": "new_server",
                    "detail": "exists on the server only - import creates the file",
                    "library": None, "record": record, "diff": [],
                })
            continue
        matched_paths.add(path)
        regenerated = mapping.instance_to_fctb(record)
        action = _classify(local_docs[path], base, regenerated)
        detail = {
            "unchanged": "in sync",
            "pull": "changed on the server - apply downloads it",
            "push": "changed locally - apply uploads it",
            "conflict": "changed on BOTH sides - choose a side below",
        }[action]
        basename = os.path.basename(path)
        items.append({
            "key": "bit:%s" % basename, "kind": "bit",
            "name": local_docs[path].get("name") or basename,
            "path": path, "basename": basename, "action": action,
            "detail": detail, "library": member_of.get(basename),
            "record": record,
            "diff": diff_docs(local_docs[path], base, regenerated)
                    if action != "unchanged" else [],
        })

    for path, doc in local_docs.items():
        if path in matched_paths:
            continue
        basename = os.path.basename(path)
        rid = mapping.fctb_record_id(doc)
        if rid and rid in state["records"] and rid not in server_record_ids:
            items.append({
                "key": "bit:%s" % basename, "kind": "bit",
                "name": doc.get("name") or basename,
                "path": path, "basename": basename, "action": "deleted_server",
                "detail": "this tool's record was deleted on the server - "
                          "upload it again, or delete the local file too",
                "library": member_of.get(basename), "record": None, "diff": [],
            })
        else:
            items.append({
                "key": "bit:%s" % basename, "kind": "bit",
                "name": doc.get("name") or basename,
                "path": path, "basename": basename, "action": "new_local",
                "detail": "not on the server yet - apply uploads it",
                "library": member_of.get(basename), "record": None, "diff": [],
            })

    # --- libraries -----------------------------------------------------------
    server_sets_by_id = {_record_id(s): s for s in server_sets}
    lib_by_id = {}
    for lpath, ldoc in local_lib_docs.items():
        sid = mapping.fctl_record_id(ldoc)
        if not sid or sid not in server_sets_by_id:
            sid = _readopt_tool_set(os.path.basename(lpath),
                                    server_sets_by_id, state) or sid
        if sid:
            lib_by_id[sid] = lpath
    matched_libs = set()
    for tool_set in server_sets:
        sid = _record_id(tool_set)
        lpath = lib_by_id.get(sid)
        if not lpath or lpath not in local_lib_docs:
            if sid in state["tool_sets"]:
                items.append({
                    "key": "server-lib:%s" % sid, "kind": "library",
                    "name": _record_name(tool_set) or "?", "path": None,
                    "basename": state["tool_sets"].get(sid),
                    "action": "deleted_local",
                    "detail": "the library file was deleted here - propagate "
                              "or restore", "library": None,
                    "record": tool_set, "diff": [],
                })
            else:
                items.append({
                    "key": "server-lib:%s" % sid, "kind": "library",
                    "name": _record_name(tool_set) or "?", "path": None,
                    "basename": None, "action": "new_server",
                    "detail": "library exists on the server only",
                    "library": None, "record": tool_set, "diff": [],
                })
            continue
        matched_libs.add(lpath)
        base = state["set_snapshots"].get(sid)
        regenerated, _ = mapping.set_to_fctl(tool_set, name_by_record_id)
        action = _classify(local_lib_docs[lpath], base, regenerated)
        basename = os.path.basename(lpath)
        detail = {
            "unchanged": "in sync",
            "pull": "membership/label changed on the server",
            "push": "changed locally - apply uploads it",
            "conflict": "changed on BOTH sides - choose a side below",
        }[action]
        delta = _membership_delta(local_lib_docs[lpath], regenerated) \
            if action != "unchanged" else ""
        if delta:
            detail += " (" + delta + ")"
        items.append({
            "key": "lib:%s" % basename, "kind": "library",
            "name": local_lib_docs[lpath].get("label") or basename,
            "path": lpath, "basename": basename, "action": action,
            "detail": detail,
            "library": None, "record": tool_set,
            "diff": diff_docs(local_lib_docs[lpath], base, regenerated)
                    if action != "unchanged" else [],
        })
    for lpath, ldoc in local_lib_docs.items():
        if lpath in matched_libs:
            continue
        basename = os.path.basename(lpath)
        sid = mapping.fctl_record_id(ldoc)
        action = "deleted_server" if (
            sid and sid in state["tool_sets"] and sid not in server_set_ids
        ) else "new_local"
        items.append({
            "key": "lib:%s" % basename, "kind": "library",
            "name": ldoc.get("label") or basename,
            "path": lpath, "basename": basename, "action": action,
            "detail": "this library was deleted on the server - upload it "
                      "again, or delete the local file too"
                      if action == "deleted_server"
                      else "not on the server yet - apply uploads it",
            "library": None, "record": None, "diff": [],
        })

    # Stable grouping: a bit sits under its owning library whether that library
    # lives locally or only on the server. Without this, server-originated bits
    # (library=None) all collapse into "not in any library" AND collide with a
    # server-only library whose basename is also None.
    server_member_group = {}
    for tool_set in server_sets:
        sid = _record_id(tool_set)
        for member in _set_members(tool_set):
            server_member_group.setdefault(
                member["tool_record_id"], "server-lib:%s" % sid)
    for it in items:
        if it["kind"] == "library":
            it["group"] = it["basename"] or it["key"]
        else:
            rid = _record_id(it["record"]) if it.get("record") else None
            it["group"] = it.get("library") or (
                server_member_group.get(rid) if rid else None)

    # Classification log — the planning side was previously silent.
    counts = {}
    for it in items:
        counts[it["action"]] = counts.get(it["action"], 0) + 1
    log("Plan: %d item(s) [%s]" % (
        len(items), ", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "none"))
    for it in items:
        if it["action"] == "unchanged":
            continue
        where = (" under %s" % it["group"]) if it.get("group") else ""
        rid = _record_id(it["record"]) if it.get("record") else None
        log("  [%s] %s '%s'%s%s" % (
            it["action"], it["kind"], it["name"], where,
            (" (record %s)" % rid[:8]) if rid else ""))

    return {"items": items, "errors": errors}


def create_tool_from_catalog(tools_dir, client, catalog_record, name=None,
                             log=lambda msg: None):
    """Create a tool from a catalog record (the M2 catalog->instance flow).

    Two effects, in order:

    1. create an UNBOUND server instance from the catalog type
       (``create_instance_from_catalog`` — a catalog is not a machine position,
       so nothing is bound);
    2. immediately materialize a local ``.fctb`` in ``<tools_dir>/Bit/``,
       pre-filled from the CATALOG's nominal geometry and linked to the new
       instance (the instance's own measured geometry is empty by M2 design —
       see :func:`mapping.catalog_to_fctb`).

    Follows the ``pull_bit`` materialize pattern (same path/dedup/state-journal
    helpers): the filename stem comes from the instance's ``client_item_id`` (or
    a slug of the instance/catalog name), an existing file is disambiguated with
    the record's short id, and the sync journal learns ``records[rid] = basename``
    so the next sync UPDATES this record rather than re-creating it.

    Returns ``{"path", "instance", "basename"}``.
    """
    catalog_id = _record_id(catalog_record)
    inst = client.create_instance_from_catalog(catalog_id, name=name)
    rid = _record_id(inst)
    doc = mapping.catalog_to_fctb(catalog_record, inst)

    bit_dir = os.path.join(tools_dir, "Bit")
    os.makedirs(bit_dir, exist_ok=True)
    cii = _client_item_id(inst) or ""
    stem = cii.rsplit(".fctb", 1)[0] or _slug(
        _record_name(inst) or _record_name(catalog_record) or "tool")
    path = os.path.join(bit_dir, stem + ".fctb")
    if os.path.exists(path):
        path = os.path.join(bit_dir, "%s_%s.fctb" % (stem, rid[:8]))
    _write_json(path, doc)
    basename = os.path.basename(path)

    # Write FreeCAD's client section (the lossless .fctb) back to the instance,
    # establishing the sync base so this freshly-created tool lands SYNCED rather
    # than as a no-base "conflict" on the next plan. Only the section is written —
    # NOT the geometry asserts: the instance's canonical geometry stays
    # deliberately empty (the nominal geometry is reachable through the catalog
    # link), exactly as the server's create-from-catalog leaves it.
    sections = mapping.record_to_instance_sections(doc, client_item_id=basename)
    client.put_instance_section(rid, sections.data, sections.client_item_id)

    state = _load_sync_state(tools_dir)
    state["records"][rid] = basename
    _save_sync_state(tools_dir, state)

    log("CREATE from catalog '%s' -> %s [%s] (record %s, unbound, synced)"
        % (_record_name(catalog_record) or "?", basename,
           doc.get("shape-type", "?"), rid[:8]))
    return {"path": path, "instance": inst, "basename": basename}


class SyncApplyError(Exception):
    """Apply-time failure for one item (others proceed)."""


def needs_shape_choice(item):
    """A bit being created/overwritten from a server record lets the user set
    its tool type first. FreeCAD fixes a bit's shape at creation, and a
    record's stored shape is often a wrong 'endmill' default from an earlier
    import — so the type must be choosable BEFORE the .fctb is written,
    including to CORRECT an existing one. Offered on any download direction."""
    return (item.get("kind") == "bit" and item.get("record") is not None
            and item.get("action") in ("new_server", "pull", "conflict"))


# ---------------------------------------------------------------------------
# Apply (mutating): execute the per-item decisions
# ---------------------------------------------------------------------------

def apply_sync(tools_dir, client, plan, decisions, shapes=None, log=lambda msg: None):
    """Execute selected plan items.

    Args:
        plan: result of plan_sync (recompute after any apply)
        decisions: {item_key: "push"|"pull"|"skip"}; absent items are
            skipped. The decision is the DIRECTION, chosen by the user; the
            plan's classification is only the suggested default. "push" uploads
            the local version (writing this client's section + asserting the
            canonical facts); "pull" writes the server version over the file.
        shapes: {item_key: shape} chosen tool type for a downloaded bit; when
            it differs from the record's stored shape it is asserted on the
            server too (healing a wrongly-stamped type, binding preserved).

    Returns {"pushed", "pulled", "skipped", "deleted", "errors": [...]}.
    """
    summary = {"pushed": 0, "pulled": 0, "skipped": 0, "deleted": 0,
               "errors": []}
    shapes = shapes or {}
    state = _load_sync_state(tools_dir)
    bit_dir = os.path.join(tools_dir, "Bit")
    lib_dir = os.path.join(tools_dir, "Library")
    os.makedirs(bit_dir, exist_ok=True)
    os.makedirs(lib_dir, exist_ok=True)

    # local record ids for library membership resolution
    record_id_by_path = {}
    for path in scan_tools_dir(tools_dir)[0]:
        try:
            doc = _read_json(path)
        except (OSError, ValueError):
            continue
        rid = mapping.fctb_record_id(doc)
        if rid:
            record_id_by_path[os.path.basename(path)] = rid

    def push_bit(item):
        doc = _read_json(item["path"])
        client_item_id = doc.get("id") or item["basename"]
        sections = mapping.record_to_instance_sections(
            doc, client_item_id=client_item_id)
        record = item["record"]
        if record:
            rid = _record_id(record)
            log("  UPLOAD bit %s -> UPDATE record %s"
                % (item["basename"], rid[:8]))
            client.put_instance_section(rid, sections.data,
                                        sections.client_item_id)
        else:
            log("  UPLOAD bit %s -> CREATE new server record" % item["basename"])
            created = client.create_instance(sections.data,
                                             sections.client_item_id)
            rid = _record_id(created)
        # FreeCAD declares the canonical facts it owns (name, geometry.shape,
        # dimensions) through the assert door — including a type correction.
        results = client.assert_instance_fields(rid, sections.asserts,
                                                actor=mapping.CLIENT_NAME)
        version = _record_version(results[-1]) if results \
            else _record_version(client.get_instance(rid))
        _writeback_identity(item["path"], rid, version)
        record_id_by_path[item["basename"]] = rid
        state["records"][rid] = item["basename"]
        summary["pushed"] += 1
        log("  uploaded %s (record %s)" % (item["basename"], rid[:8]))

    def pull_bit(item):
        record = item["record"]
        rid = _record_id(record)
        chosen = shapes.get(item["key"])
        # A corrected type is healed on the SERVER first (assert the canonical
        # shape, keeping the record id/binding); re-fetch so regeneration sees
        # it. A no-op choice touches nothing.
        if chosen and chosen != record_shape(record):
            client.assert_instance(rid, "geometry.shape", chosen,
                                   actor=mapping.CLIENT_NAME)
            record = client.get_instance(rid)

        regenerated = mapping.instance_to_fctb(record)
        path = item["path"]
        if not path:
            cii = _client_item_id(record) or ""
            stem = cii.rsplit(".fctb", 1)[0] or _slug(_record_name(record) or "tool")
            path = os.path.join(bit_dir, stem + ".fctb")
            if os.path.exists(path):
                path = os.path.join(bit_dir, "%s_%s.fctb" % (stem, rid[:8]))
        _write_json(path, regenerated)
        basename = os.path.basename(path)
        record_id_by_path[basename] = rid
        state["records"][rid] = basename
        summary["pulled"] += 1
        log("  DOWNLOAD record %s -> %s [%s]%s (stays linked to the record)"
            % (rid[:8], basename, regenerated.get("shape-type", "?"),
               " type set to %s" % chosen if chosen else ""))

        # Heal the server's client section too, so its stored .fctb matches the
        # corrected type and the next sync converges (no pending push-back).
        if chosen and chosen != mapping.fctb_shape(_base_fctb(record)):
            healed = mapping.record_to_instance_sections(
                regenerated, client_item_id=basename)
            client.put_instance_section(rid, healed.data, healed.client_item_id)
            log("  corrected server record %s: shape -> %s (binding kept)"
                % (rid[:8], chosen))

    def push_library(item):
        doc = _read_json(item["path"])
        sections = mapping.fctl_to_set_sections(doc, record_id_by_path)
        for missing in sections.unresolved:
            summary["errors"].append(
                "%s: member %s has no server record - upload it first"
                % (item["basename"], missing))
        record = item["record"]
        if record:
            rid = _record_id(record)
            client.put_set_section(rid, sections.data, sections.client_item_id)
        else:
            created = client.create_set(sections.data, sections.client_item_id)
            rid = _record_id(created)
        for path, value in sections.asserts:
            client.assert_set(rid, path, value, actor=mapping.CLIENT_NAME)
        result = client.set_members(rid, sections.members,
                                    actor=mapping.CLIENT_NAME)
        _writeback_identity(item["path"], rid, _record_version(result))
        state["tool_sets"][rid] = item["basename"]
        state["set_snapshots"][rid] = _sans_smooth(_read_json(item["path"]))
        summary["pushed"] += 1
        log("uploaded %s" % item["basename"])

    def pull_library(item):
        record = item["record"]
        rid = _record_id(record)
        path_by_record_id = {v: k for k, v in record_id_by_path.items()}
        regenerated, unresolved = mapping.set_to_fctl(record, path_by_record_id)
        for member_id in unresolved:
            summary["errors"].append(
                "%s: member %s has no local file - download it first"
                % (item["name"], member_id))
        path = item["path"] or os.path.join(
            lib_dir, _slug(_record_name(record) or "library") + ".fctl")
        _write_json(path, regenerated)
        state["tool_sets"][rid] = os.path.basename(path)
        state["set_snapshots"][rid] = _sans_smooth(regenerated)
        summary["pulled"] += 1
        log("downloaded %s" % os.path.basename(path))

    # bits before libraries so membership resolves
    ordered = sorted(plan["items"], key=lambda i: 0 if i["kind"] == "bit" else 1)
    active = {k: v for k, v in decisions.items() if v != "skip"}
    log("Applying %d decision(s): %s" % (
        len(active),
        ", ".join("%s=%s" % (k, v) for k, v in sorted(active.items())) or "none"))
    for item in ordered:
        decision = decisions.get(item["key"], "skip")
        if decision == "skip" or item["action"] == "unchanged":
            if decision == "skip" and item["action"] != "unchanged":
                summary["skipped"] += 1
            continue
        log("• %s '%s' [%s] -> %s" % (item["kind"], item["name"],
                                      item["action"], decision))
        try:
            if item["action"] == "deleted_local" and decision == "push":
                # explicit human choice: propagate the local deletion
                rid = _record_id(item["record"])
                if item["kind"] == "bit":
                    client.delete_instance(rid)
                    state["records"].pop(rid, None)
                else:
                    client.delete_set(rid)
                    state["tool_sets"].pop(rid, None)
                    state["set_snapshots"].pop(rid, None)
                summary["deleted"] += 1
                log("deleted on server: %s" % item["name"])
            elif item["action"] == "deleted_server" and decision == "pull":
                # explicit human choice: delete the local file too
                os.remove(item["path"])
                if item["kind"] == "bit":
                    state["records"] = {k: v for k, v in state["records"].items()
                                        if v != item["basename"]}
                else:
                    state["tool_sets"] = {k: v for k, v in state["tool_sets"].items()
                                          if v != item["basename"]}
                summary["deleted"] += 1
                log("deleted local file: %s" % item["basename"])
            elif decision == "push" and item["path"] is None:
                summary["errors"].append(
                    "%s: nothing local to upload" % item["name"])
            elif decision == "pull" and item["record"] is None:
                summary["errors"].append(
                    "%s: nothing on the server to download" % item["name"])
            elif item["kind"] == "bit":
                push_bit(item) if decision == "push" else pull_bit(item)
            else:
                push_library(item) if decision == "push" else pull_library(item)
        except (SyncApplyError, SmoothError) as e:
            summary["errors"].append("%s: %s" % (item["name"], e))
            log("  ! %s: %s" % (item["name"], e))

    # backfill the journal with everything currently matched, then persist
    for item in plan["items"]:
        if item["path"] and item["record"]:
            bucket = "records" if item["kind"] == "bit" else "tool_sets"
            state[bucket][_record_id(item["record"])] = item["basename"]
    _save_sync_state(tools_dir, state)
    return summary
