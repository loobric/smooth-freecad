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

    # --- bits ---------------------------------------------------------------
    for path in bit_paths:
        basename = os.path.basename(path)
        try:
            doc = _read_json(path)
        except (OSError, ValueError) as e:
            summary["errors"].append("%s: unreadable (%s)" % (basename, e))
            continue
        payload, prior_id = mapping.fctb_to_record(doc)

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
