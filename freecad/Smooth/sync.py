# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Sync orchestration — export FreeCAD tool dirs to a Smooth server.

Pure logic over the filesystem and an injected SmoothClient; no FreeCAD
imports, fully testable headless. Implements smooth-freecad#5 (export);
the import direction is #6.

Identity rules (the production lesson — never create duplicates):
- A bit/library that was exported before carries its server id in the
  additive 'smooth' key; re-export PATCHes that id using the SERVER's
  current version (fetch-before-patch), never blind-POSTs.
- After a successful create, the server id is written back into the file
  immediately, so even a crashed sync never double-creates.
- Bits export before libraries: library membership needs record ids.
"""
import json
import os

from . import mapping


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, doc):
    """Write FreeCAD-style JSON (2-space indent, trailing newline)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _writeback_identity(path, key, server_id, version):
    """Inject/refresh the additive 'smooth' key in a tool file."""
    doc = _read_json(path)
    doc["smooth"] = {key: server_id, "version": version}
    _write_json(path, doc)


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


def export_tools(tools_dir, client, log=lambda msg: None):
    """Export all bits then all libraries to the server.

    Returns a summary dict:
        {"created": int, "updated": int, "errors": [str],
         "record_id_by_path": {basename: record_id}}

    Assumptions:
    - Bits with a 'smooth' key PATCH their record using the server's
      current version; unknown ids on the server (deleted there) fall
      back to create
    - Server-side per-item errors are reported, never silently dropped
    """
    summary = {"created": 0, "updated": 0, "errors": [], "record_id_by_path": {}}
    bit_paths, lib_paths = scan_tools_dir(tools_dir)
    server_records = {r["id"]: r for r in client.list_records()}

    # Re-adoption index (field finding: FreeCAD's ToolBit editor drops
    # unknown top-level keys on save, destroying the 'smooth' identity
    # key). The .fctb 'id' field IS preserved by the editor and we store
    # it verbatim server-side - an exact-identifier match, not a heuristic.
    records_by_fctb_id = {}
    for r in server_records.values():
        fid = ((r.get("extra") or {}).get("freecad", {}).get("fctb", {})).get("id")
        if fid:
            records_by_fctb_id.setdefault(fid, []).append(r)

    # --- bits ---------------------------------------------------------------
    for path in bit_paths:
        basename = os.path.basename(path)
        try:
            doc = _read_json(path)
        except (OSError, ValueError) as e:
            summary["errors"].append("%s: unreadable (%s)" % (basename, e))
            continue
        payload, prior_id = mapping.fctb_to_record(doc)
        payload["extra"]["freecad"]["filename"] = basename

        if not prior_id or prior_id not in server_records:
            # smooth key missing (editor save) or stale: re-adopt by exact
            # .fctb id before considering a create
            fid = payload["extra"]["freecad"]["fctb"].get("id")
            candidates = records_by_fctb_id.get(fid, []) if fid else []
            if len(candidates) == 1:
                if prior_id:
                    log("%s: stale id; re-adopted by fctb id '%s'" % (basename, fid))
                else:
                    log("%s: identity key missing (editor save?); re-adopted "
                        "by fctb id '%s'" % (basename, fid))
                prior_id = candidates[0]["id"]
            elif len(candidates) > 1:
                summary["errors"].append(
                    "%s: ambiguous - %d server records share fctb id '%s'; "
                    "not guessing" % (basename, len(candidates), fid)
                )
                continue

        if prior_id and prior_id in server_records:
            current = server_records[prior_id]
            result = client.update_records([{
                "id": prior_id, "version": current["version"], **payload
            }])
            action = "updated"
        else:
            if prior_id:
                log("%s: server record %s gone; recreating" % (basename, prior_id))
            result = client.create_records([payload])
            action = "created"

        for error in result.get("errors", []):
            summary["errors"].append("%s: %s" % (basename, error.get("message")))
        items = result.get("items", [])
        if not items:
            continue
        record = items[0]
        summary[action] += 1
        summary["record_id_by_path"][basename] = record["id"]
        _writeback_identity(path, "record_id", record["id"], record["version"])
        log("%s %s -> %s" % (action, basename, record["id"][:8]))

    # include previously-exported bits that errored or were skipped this run
    for path in bit_paths:
        basename = os.path.basename(path)
        if basename not in summary["record_id_by_path"]:
            try:
                _, prior_id = mapping.fctb_to_record(_read_json(path))
            except (OSError, ValueError):
                continue
            if prior_id:
                summary["record_id_by_path"][basename] = prior_id

    # --- libraries ------------------------------------------------------------
    server_libraries = {l["id"]: l for l in client.list_libraries()}
    for path in lib_paths:
        basename = os.path.basename(path)
        try:
            doc = _read_json(path)
        except (OSError, ValueError) as e:
            summary["errors"].append("%s: unreadable (%s)" % (basename, e))
            continue
        payload, unresolved, prior_id = mapping.fctl_to_library(
            doc, summary["record_id_by_path"]
        )
        for missing in unresolved:
            summary["errors"].append(
                "%s: member %s has no server record (bit export failed?)"
                % (basename, missing)
            )

        if prior_id and prior_id in server_libraries:
            current = server_libraries[prior_id]
            result = client.update_libraries([{
                "id": prior_id, "version": current["version"], **payload
            }])
            action = "updated"
        else:
            if prior_id:
                log("%s: server library %s gone; recreating" % (basename, prior_id))
            result = client.create_libraries([payload])
            action = "created"

        for error in result.get("errors", []):
            summary["errors"].append("%s: %s" % (basename, error.get("message")))
        items = result.get("items", [])
        if not items:
            continue
        library = items[0]
        summary[action] += 1
        _writeback_identity(path, "library_id", library["id"], library["version"])
        log("%s %s -> %s" % (action, basename, library["id"][:8]))

    return summary


# ---------------------------------------------------------------------------
# Import (server -> FreeCAD)
# ---------------------------------------------------------------------------

def _sans_smooth(doc):
    """Copy of a tool document without the identity plumbing key."""
    return {k: v for k, v in doc.items() if k != "smooth"}


def _slug(name):
    """Filesystem-safe filename stem from a record name."""
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "tool"


def import_tools(tools_dir, client, log=lambda msg: None):
    """Import server state into the FreeCAD tool directories.

    Three-way merge per file, using the server-stored verbatim document as
    the base (what the server last saw from FreeCAD):

    - local == regenerated            -> unchanged (refresh stamp if stale)
    - local == base, server changed   -> write server version
    - server == base, local changed   -> keep local ("pending export")
    - both changed                    -> CONFLICT: never overwritten, reported

    Returns a summary dict:
        {"written": int, "unchanged": int, "pending_export": int,
         "conflicts": [str], "errors": [str]}
    """
    summary = {"written": 0, "unchanged": 0, "pending_export": 0,
               "conflicts": [], "errors": []}
    bit_dir = os.path.join(tools_dir, "Bit")
    lib_dir = os.path.join(tools_dir, "Library")
    os.makedirs(bit_dir, exist_ok=True)
    os.makedirs(lib_dir, exist_ok=True)

    bit_paths, lib_paths = scan_tools_dir(tools_dir)

    # Index local files by server identity (smooth key, then fctb id)
    local_by_record_id = {}
    local_fctb_ids = {}
    local_by_name = {}
    for path in bit_paths:
        local_by_name[os.path.basename(path)] = path
        try:
            doc = _read_json(path)
        except (OSError, ValueError) as e:
            summary["errors"].append("%s: unreadable (%s)" % (os.path.basename(path), e))
            continue
        rid = (doc.get("smooth") or {}).get("record_id")
        if rid:
            local_by_record_id[rid] = path
        if doc.get("id"):
            local_fctb_ids.setdefault(doc["id"], path)

    path_by_record_id = {}

    # --- bits ----------------------------------------------------------------
    for record in client.list_records():
        meta = (record.get("extra") or {}).get("freecad", {})
        base = meta.get("fctb")
        regenerated = mapping.record_to_fctb(record)
        fctb_id = (base or {}).get("id")
        path = (local_by_record_id.get(record["id"])
                or (fctb_id and local_fctb_ids.get(fctb_id))
                or (meta.get("filename") and local_by_name.get(meta["filename"])))

        if not path:
            # new from the server: prefer its original filename, else derive
            stem = (meta.get("filename") or "").rsplit(".fctb", 1)[0] \
                or fctb_id or _slug(record.get("name", "tool"))
            path = os.path.join(bit_dir, stem + ".fctb")
            if os.path.exists(path):
                path = os.path.join(bit_dir, "%s_%s.fctb" % (stem, record["id"][:8]))
            _write_json(path, regenerated)
            summary["written"] += 1
            path_by_record_id[record["id"]] = os.path.basename(path)
            log("new from server: %s" % os.path.basename(path))
            continue

        path_by_record_id[record["id"]] = os.path.basename(path)
        basename = os.path.basename(path)
        try:
            local = _read_json(path)
        except (OSError, ValueError) as e:
            summary["errors"].append("%s: unreadable (%s)" % (basename, e))
            continue

        local_doc = _sans_smooth(local)
        regen_doc = _sans_smooth(regenerated)
        if local_doc == regen_doc:
            summary["unchanged"] += 1
            if (local.get("smooth") or {}).get("version") != record.get("version"):
                _writeback_identity(path, "record_id", record["id"], record["version"])
            continue
        if base is not None and local_doc == _sans_smooth(base):
            _write_json(path, regenerated)
            summary["written"] += 1
            log("updated from server: %s" % basename)
            continue
        if base is not None and regen_doc == _sans_smooth(base):
            summary["pending_export"] += 1
            log("local changes pending export, kept: %s" % basename)
            continue
        summary["conflicts"].append(
            "%s: changed locally AND on the server - not overwritten. "
            "Export to keep the local version, or delete the file and "
            "re-import to take the server's." % basename
        )

    # --- libraries -------------------------------------------------------------
    local_lib_by_id = {}
    for path in lib_paths:
        try:
            doc = _read_json(path)
        except (OSError, ValueError):
            continue
        lid = (doc.get("smooth") or {}).get("library_id")
        if lid:
            local_lib_by_id[lid] = path

    for library in client.list_libraries():
        meta = (library.get("extra") or {}).get("freecad", {})
        base = meta.get("fctl")
        regenerated, unresolved = mapping.library_to_fctl(library, path_by_record_id)
        for rid in unresolved:
            summary["errors"].append(
                "library '%s': member %s has no local file" % (library.get("name"), rid)
            )
        path = local_lib_by_id.get(library["id"])

        if not path:
            stem = _slug(library.get("name", "library"))
            path = os.path.join(lib_dir, stem + ".fctl")
            if os.path.exists(path):
                path = os.path.join(lib_dir, "%s_%s.fctl" % (stem, library["id"][:8]))
            _write_json(path, regenerated)
            summary["written"] += 1
            log("new from server: %s" % os.path.basename(path))
            continue

        basename = os.path.basename(path)
        try:
            local = _read_json(path)
        except (OSError, ValueError) as e:
            summary["errors"].append("%s: unreadable (%s)" % (basename, e))
            continue

        local_doc = _sans_smooth(local)
        regen_doc = _sans_smooth(regenerated)
        if local_doc == regen_doc:
            summary["unchanged"] += 1
            if (local.get("smooth") or {}).get("version") != library.get("version"):
                _writeback_identity(path, "library_id", library["id"], library["version"])
            continue
        if base is not None and local_doc == _sans_smooth(base):
            _write_json(path, regenerated)
            summary["written"] += 1
            log("updated from server: %s" % basename)
            continue
        if base is not None and regen_doc == _sans_smooth(base):
            summary["pending_export"] += 1
            log("local changes pending export, kept: %s" % basename)
            continue
        summary["conflicts"].append(
            "%s: changed locally AND on the server - not overwritten." % basename
        )

    return summary


# ---------------------------------------------------------------------------
# Plan / Apply (smooth-freecad#7): preview-first sync with per-item control
# ---------------------------------------------------------------------------

def _semantic(doc):
    """Canonical flat form of a document for semantic equality."""
    return {k: _canonical_value(v) for k, v in _flatten(doc).items()}


def _classify(local_doc, base, regenerated):
    """3-way classification of one file (shared by import and the planner).

    Comparison is SEMANTIC: quantity formatting churn from FreeCAD's
    editor ('6.0000 mm' -> '6.00 mm') does not count as change.

    Returns one of: "unchanged", "pull" (server changed), "push" (local
    changed), "conflict" (both changed).
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


STATE_BASENAME = ".smooth_state.json"


def _load_sync_state(tools_dir):
    """This install's memory of what it has synced - the only way to tell
    'deleted here' from 'new on the server' (and vice versa). Lives with
    the library it describes."""
    try:
        with open(os.path.join(tools_dir, STATE_BASENAME)) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    state.setdefault("records", {})
    state.setdefault("libraries", {})
    return state


def _save_sync_state(tools_dir, state):
    try:
        with open(os.path.join(tools_dir, STATE_BASENAME), "w") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass


def plan_sync(tools_dir, client):
    """Compute the sync plan without touching anything.

    Returns {"items": [...], "errors": [...]}; each item:
        {"key": str,                # stable handle for apply decisions
         "kind": "bit"|"library",
         "name": str, "path": str|None, "basename": str|None,
         "action": "unchanged"|"push"|"pull"|"new_local"|"new_server"
                   |"conflict",
         "detail": str,
         "library": str|None,       # owning .fctl basename for bits
         "record": dict|None}       # server object when one exists
    """
    items = []
    errors = []
    state = _load_sync_state(tools_dir)
    bit_paths, lib_paths = scan_tools_dir(tools_dir)
    server_records = client.list_records()
    server_libraries = client.list_libraries()
    server_record_ids = {r["id"] for r in server_records}
    server_library_ids = {l["id"] for l in server_libraries}

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
        rid = (doc.get("smooth") or {}).get("record_id")
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

    matched_paths = set()
    for record in server_records:
        meta = (record.get("extra") or {}).get("freecad", {})
        base = meta.get("fctb")
        fctb_id = (base or {}).get("id")
        path = (by_record_id.get(record["id"])
                or (fctb_id and by_fctb_id.get(fctb_id))
                or (meta.get("filename") and by_filename.get(meta["filename"])))
        if not path or path not in local_docs:
            if record["id"] in state["records"]:
                items.append({
                    "key": "server:%s" % record["id"], "kind": "bit",
                    "name": record.get("name", "?"), "path": None,
                    "basename": meta.get("filename"),
                    "action": "deleted_local",
                    "detail": "the file '%s' was deleted here - propagate the "
                              "deletion to the server, or restore the file"
                              % state["records"][record["id"]],
                    "library": None, "record": record, "diff": [],
                })
            else:
                items.append({
                    "key": "server:%s" % record["id"], "kind": "bit",
                    "name": record.get("name", "?"), "path": None,
                    "basename": meta.get("filename"),
                    "action": "new_server",
                    "detail": "exists on the server only - import creates the file",
                    "library": None, "record": record, "diff": [],
                })
            continue
        matched_paths.add(path)
        regenerated = mapping.record_to_fctb(record)
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
        rid = (doc.get("smooth") or {}).get("record_id")
        if rid and rid in state["records"].keys() and rid not in server_record_ids:
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

    # libraries
    lib_by_id = {}
    for lpath, ldoc in local_lib_docs.items():
        lid = (ldoc.get("smooth") or {}).get("library_id")
        if lid:
            lib_by_id[lid] = lpath
    matched_libs = set()
    for library in server_libraries:
        lpath = lib_by_id.get(library["id"])
        if not lpath or lpath not in local_lib_docs:
            if library["id"] in state["libraries"]:
                items.append({
                    "key": "server-lib:%s" % library["id"], "kind": "library",
                    "name": library.get("name", "?"), "path": None,
                    "basename": None, "action": "deleted_local",
                    "detail": "the library file was deleted here - propagate "
                              "or restore", "library": None,
                    "record": library, "diff": [],
                })
            else:
                items.append({
                    "key": "server-lib:%s" % library["id"], "kind": "library",
                    "name": library.get("name", "?"), "path": None,
                    "basename": None, "action": "new_server",
                    "detail": "library exists on the server only",
                    "library": None, "record": library, "diff": [],
                })
            continue
        matched_libs.add(lpath)
        base = (library.get("extra") or {}).get("freecad", {}).get("fctl")
        # membership resolution uses local filenames where known
        path_map = {}
        for record in server_records:
            meta = (record.get("extra") or {}).get("freecad", {})
            rpath = (by_record_id.get(record["id"])
                     or (meta.get("filename") and by_filename.get(meta["filename"])))
            path_map[record["id"]] = os.path.basename(rpath) if rpath \
                else (meta.get("filename") or record["id"][:8] + ".fctb")
        regenerated, _ = mapping.library_to_fctl(library, path_map)
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
            "library": None, "record": library,
            "diff": diff_docs(local_lib_docs[lpath], base, regenerated)
                    if action != "unchanged" else [],
        })
    for lpath, ldoc in local_lib_docs.items():
        if lpath in matched_libs:
            continue
        basename = os.path.basename(lpath)
        lid = (ldoc.get("smooth") or {}).get("library_id")
        action = "deleted_server" if (
            lid and lid in state["libraries"] and lid not in server_library_ids
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

    return {"items": items, "errors": errors}


class SyncApplyError(Exception):
    """Apply-time failure for one item (others proceed)."""


def apply_sync(tools_dir, client, plan, decisions, log=lambda msg: None):
    """Execute selected plan items.

    Args:
        plan: result of plan_sync (recompute after any apply)
        decisions: {item_key: "push"|"pull"|"skip"}; absent items are
            skipped. The decision is the DIRECTION, chosen by the user;
            the plan's classification is only the suggested default.
            "push" uploads the local version (force, using the server's
            current version - an explicit human decision); "pull" writes
            the server version over the local file.

    Returns {"pushed": n, "pulled": n, "skipped": n, "errors": [...]}.

    Assumptions:
    - keep_local force-uploads using the server's CURRENT version (an
      explicit human decision, so overruling the server copy is correct)
    - take_server rewrites the local file from the server record
    - library uploads re-resolve membership from local files at apply time
    """
    summary = {"pushed": 0, "pulled": 0, "skipped": 0, "deleted": 0,
               "errors": []}
    state = _load_sync_state(tools_dir)
    by_key = {i["key"]: i for i in plan["items"]}
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
        rid = (doc.get("smooth") or {}).get("record_id")
        if rid:
            record_id_by_path[os.path.basename(path)] = rid

    def push_bit(item, force=False):
        doc = _read_json(item["path"])
        payload, _ = mapping.fctb_to_record(doc)
        payload["extra"]["freecad"]["filename"] = item["basename"]
        if item["record"]:
            result = client.update_records([{
                "id": item["record"]["id"],
                "version": item["record"]["version"], **payload}])
        else:
            result = client.create_records([payload])
        for error in result.get("errors", []):
            summary["errors"].append("%s: %s" % (item["basename"], error.get("message")))
        if result.get("items"):
            rec = result["items"][0]
            _writeback_identity(item["path"], "record_id", rec["id"], rec["version"])
            record_id_by_path[item["basename"]] = rec["id"]
            state["records"][rec["id"]] = item["basename"]
            summary["pushed"] += 1
            log("uploaded %s" % item["basename"])

    def pull_bit(item):
        regenerated = mapping.record_to_fctb(item["record"])
        path = item["path"]
        if not path:
            meta = (item["record"].get("extra") or {}).get("freecad", {})
            stem = (meta.get("filename") or "").rsplit(".fctb", 1)[0] \
                or _slug(item["record"].get("name", "tool"))
            path = os.path.join(bit_dir, stem + ".fctb")
            if os.path.exists(path):
                path = os.path.join(bit_dir, "%s_%s.fctb"
                                    % (stem, item["record"]["id"][:8]))
        _write_json(path, regenerated)
        record_id_by_path[os.path.basename(path)] = item["record"]["id"]
        state["records"][item["record"]["id"]] = os.path.basename(path)
        summary["pulled"] += 1
        log("downloaded %s" % os.path.basename(path))

    def push_library(item):
        doc = _read_json(item["path"])
        payload, unresolved, _ = mapping.fctl_to_library(doc, record_id_by_path)
        for missing in unresolved:
            summary["errors"].append(
                "%s: member %s has no server record - upload it first"
                % (item["basename"], missing))
        if item["record"]:
            result = client.update_libraries([{
                "id": item["record"]["id"],
                "version": item["record"]["version"], **payload}])
        else:
            result = client.create_libraries([payload])
        for error in result.get("errors", []):
            summary["errors"].append("%s: %s" % (item["basename"], error.get("message")))
        if result.get("items"):
            lib = result["items"][0]
            _writeback_identity(item["path"], "library_id", lib["id"], lib["version"])
            state["libraries"][lib["id"]] = item["basename"]
            summary["pushed"] += 1
            log("uploaded %s" % item["basename"])

    def pull_library(item):
        path_map = {rid: name for name, rid in record_id_by_path.items()}
        regenerated, unresolved = mapping.library_to_fctl(item["record"], path_map)
        for rid in unresolved:
            summary["errors"].append(
                "%s: member %s has no local file - download it first"
                % (item["name"], rid))
        path = item["path"] or os.path.join(
            lib_dir, _slug(item["record"].get("name", "library")) + ".fctl")
        _write_json(path, regenerated)
        state["libraries"][item["record"]["id"]] = os.path.basename(path)
        summary["pulled"] += 1
        log("downloaded %s" % os.path.basename(path))

    # bits before libraries so membership resolves
    ordered = sorted(plan["items"], key=lambda i: 0 if i["kind"] == "bit" else 1)
    for item in ordered:
        decision = decisions.get(item["key"], "skip")
        if decision == "skip" or item["action"] == "unchanged":
            if decision == "skip" and item["action"] != "unchanged":
                summary["skipped"] += 1
            continue
        try:
            if item["action"] == "deleted_local" and decision == "push":
                # explicit human choice: propagate the local deletion
                if item["kind"] == "bit":
                    client.delete_records([item["record"]["id"]])
                    state["records"].pop(item["record"]["id"], None)
                else:
                    client.delete_libraries([item["record"]["id"]])
                    state["libraries"].pop(item["record"]["id"], None)
                summary["deleted"] += 1
                log("deleted on server: %s" % item["name"])
            elif item["action"] == "deleted_server" and decision == "pull":
                # explicit human choice: delete the local file too
                os.remove(item["path"])
                if item["kind"] == "bit":
                    state["records"] = {k: v for k, v in state["records"].items()
                                        if v != item["basename"]}
                else:
                    state["libraries"] = {k: v for k, v in state["libraries"].items()
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
        except SyncApplyError as e:
            summary["errors"].append(str(e))

    # backfill the journal with everything currently matched, then persist
    for item in plan["items"]:
        if item["path"] and item["record"]:
            bucket = "records" if item["kind"] == "bit" else "libraries"
            state[bucket][item["record"]["id"]] = item["basename"]
    _save_sync_state(tools_dir, state)
    return summary
