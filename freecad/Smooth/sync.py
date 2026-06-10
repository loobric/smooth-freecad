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
