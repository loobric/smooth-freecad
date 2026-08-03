# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Create or update a Loobric **ToolSet** from the tools of a FreeCAD CAM Job.

The claims a set carries are the T-numbers posted G-code will call — and a
Job's ToolControllers ARE those numbers, so a job-derived set makes the claim
source the actual job instead of a library whose numbers silently diverge
(README 'Known limitations'). Deliberately, **no .fctl is ever written**: the
user's FreeCAD tool libraries are their own artifact, never polluted by this
flow. The set is created server-side only, stamped with job provenance in
``clients.freecad.data`` (``{origin: "job", document, job}``) — which is also
what makes the sync plan render it read-only (sync.plan_sync's ``job_set``
action) instead of offering a download that would materialize a library file.

Pure logic — no FreeCAD imports. The GUI command (LoobricCommands.py) extracts
each ToolController into a plain dict and hands it here::

    {"name":    the toolbit's label,
     "number":  ToolController.ToolNumber (the claim),
     "bit_id":  the toolbit's ToolBitID — the asset id, i.e. the FILENAME STEM
                of the .fctb it came from (FreeCAD's asset deserializer
                overwrites any in-file "id" with the asset id on load),
     "embedded": the job's embedded toolbit serialized to fctb form (or None)}

Membership rules (grilled & locked 2026-07-30):

- a toolbit that resolves to no ``.fctb`` in the asset dir is REFUSED (save it
  to the tool library first) — reported, never silent;
- the same tool under two different T-numbers is REFUSED (renumber the job);
- two DIFFERENT tools claiming one T-number BLOCKS the whole command
  (:class:`JobSetError`) — the job as posted cannot run, a client-side
  interlock MAPPING_PLAN explicitly permits;
- a member not on the server yet is uploaded as part of apply (listed first);
- an embedded copy that drifted from its asset file is included with a warning.

Re-runs replace members wholesale — the job is the source of truth. Identity
lives on the Job object (the ``LoobricSetId`` property the GUI writes); the
provenance stamp is the fallback matcher when that property is lost.
"""
import os

from . import mapping, sync


class JobSetError(Exception):
    """A job error that blocks set creation (two tools claiming one number)."""


def _record_id(record):
    return (record.get("internal") or {}).get("id")


def _set_name(record):
    """A set's canonical name value, or None."""
    name = (record.get("canonical") or {}).get("name")
    return name.get("value") if isinstance(name, dict) else name


def _fmt_numbers(numbers):
    return " and ".join("T%s" % n for n in numbers)


def _tool_data(doc):
    """A .fctb document without its identity plumbing, for drift comparison.
    ``id`` is excluded: FreeCAD stamps the embedded copy's id with the asset's
    filename stem at load, so it routinely differs from (or is absent in) the
    file without the TOOL having drifted at all."""
    return {k: v for k, v in (doc or {}).items() if k != "id"}


# ---------------------------------------------------------------------------
# Plan (pure): job tools -> members / excluded, or a blocking error
# ---------------------------------------------------------------------------

def plan_job_set(controllers, tools_dir, server_records):
    """Resolve a job's tools into ToolSet membership.

    Args:
        controllers: the extracted ToolController dicts (see module docstring).
        tools_dir: the FreeCAD Tools directory the sync engine works over.
        server_records: ``client.list_instances()`` — for update-vs-upload.

    Returns ``{"members": [...], "excluded": [...]}``:

    - each member: ``{"name", "number", "path", "basename", "tool_record_id"
      (None until uploaded), "needs_upload", "drifted"}``, ordered by number;
    - each exclusion: ``{"name", "reason"}`` — shown in the confirmation,
      never silently dropped.

    Raises :class:`JobSetError` when two different tools claim one number.
    """
    docs_by_id = {}
    for path in sync.scan_tools_dir(tools_dir)[0]:
        try:
            doc = sync._read_json(path)
        except (OSError, ValueError):
            continue
        # A ToolBitID is the asset id — the FILENAME STEM — because FreeCAD's
        # asset deserializer overwrites any in-file "id" with it on load. The
        # stem is therefore the primary match key; the in-file "id" is kept
        # as a fallback for hand-managed files.
        stem = os.path.splitext(os.path.basename(path))[0]
        docs_by_id.setdefault(stem, (path, doc))
        if doc.get("id"):
            docs_by_id.setdefault(doc["id"], (path, doc))

    server_ids = {_record_id(r) for r in server_records}
    by_client_item_id = {}
    for record in server_records:
        cii = ((record.get("clients") or {}).get(mapping.CLIENT_NAME)
               or {}).get("client_item_id")
        if cii:
            by_client_item_id.setdefault(cii, _record_id(record))

    # One entry per distinct toolbit: the same tool in several controllers
    # (different feeds/speeds) is routine and collapses to one member.
    by_bit = {}
    order = []
    for tc in controllers:
        bit_id = tc.get("bit_id")
        key = bit_id or "unresolved:%s" % tc.get("name")
        if key not in by_bit:
            by_bit[key] = {"name": tc.get("name"), "bit_id": bit_id,
                           "embedded": tc.get("embedded"), "numbers": []}
            order.append(key)
        if tc.get("number") not in by_bit[key]["numbers"]:
            by_bit[key]["numbers"].append(tc.get("number"))

    members = []
    excluded = []
    for key in order:
        bit = by_bit[key]
        name = bit["name"] or "?"
        if bit["bit_id"] is None or bit["bit_id"] not in docs_by_id:
            excluded.append({
                "name": name,
                "reason": "not found in the tool library — save this tool to "
                          "your tool library first"})
            continue
        if len(bit["numbers"]) > 1:
            excluded.append({
                "name": name,
                "reason": "used as %s — renumber the job so this tool claims "
                          "one number" % _fmt_numbers(sorted(bit["numbers"]))})
            continue
        path, doc = docs_by_id[bit["bit_id"]]
        basename = os.path.basename(path)
        rid = mapping.fctb_record_id(doc)
        if rid not in server_ids:
            rid = (by_client_item_id.get(doc.get("id"))
                   or by_client_item_id.get(basename))
        drifted = (bit["embedded"] is not None
                   and sync._semantic(_tool_data(bit["embedded"]))
                   != sync._semantic(_tool_data(doc)))
        members.append({
            "name": name, "number": bit["numbers"][0],
            "path": path, "basename": basename,
            "tool_record_id": rid, "needs_upload": rid is None,
            "drifted": drifted,
        })

    claimants = {}
    for m in members:
        claimants.setdefault(m["number"], []).append(m["name"])
    clashes = {n: names for n, names in claimants.items() if len(names) > 1}
    if clashes:
        lines = ["T%s claimed by both %s" % (n, " and ".join("'%s'" % x for x in names))
                 for n, names in sorted(clashes.items())]
        raise JobSetError(
            "%s — renumber the job. The job as posted cannot run: the machine "
            "cannot hold two tools at one number." % "; ".join(lines))

    members.sort(key=lambda m: (m["number"] is None, m["number"]))
    return {"members": members, "excluded": excluded}


# ---------------------------------------------------------------------------
# Delta (pure): what a wholesale replace will change on an existing set
# ---------------------------------------------------------------------------

def member_delta(set_record, members):
    """Human-readable lines describing what replacing an existing set's
    members with ``members`` changes — shown in the confirmation on a re-run.
    Wholesale replace is the contract (the job is the source of truth), so
    members added by other actors are listed as dropped, not preserved."""
    old = {m["tool_record_id"]: m["number"]
           for m in sync._set_members(set_record or {})}
    new = {m["tool_record_id"]: m for m in members if m["tool_record_id"]}
    lines = []
    for rid, member in new.items():
        if rid not in old:
            lines.append("+ T%s  %s" % (member["number"], member["name"]))
        elif old[rid] != member["number"]:
            lines.append("T%s → T%s  %s"
                         % (old[rid], member["number"], member["name"]))
    known = {m["tool_record_id"] for m in members}
    for rid, number in old.items():
        if rid not in known:
            lines.append("− T%s  (no longer in the job)" % number)
    return lines


# ---------------------------------------------------------------------------
# Apply (mutating): uploads, create/adopt, provenance, members, name
# ---------------------------------------------------------------------------

def claim_set_name(client, set_id, name):
    """Assert the set's canonical name unless ANOTHER set already uses it.

    The server doesn't enforce unique set names, so the collision check is
    client-side — never silently mint a near-duplicate name the operator can't
    tell apart. Returns True when claimed; False on collision (the caller
    re-prompts)."""
    for record in client.list_sets():
        if _record_id(record) != set_id and _set_name(record) == name:
            return False
    client.assert_set(set_id, "name", name, actor=mapping.CLIENT_NAME)
    return True


def apply_job_set(client, tools_dir, plan, name, provenance, set_id=None,
                  log=lambda msg: None):
    """Execute a job-set plan: upload missing bits, create/adopt the set,
    stamp job provenance, replace the members wholesale, claim the name.

    Args:
        plan: :func:`plan_job_set`'s result (members with any exclusions
            already reported to the user).
        name: the set name the user confirmed.
        provenance: ``{"document", "job"}`` — stamped into
            ``clients.freecad.data`` (with ``origin: "job"``); also the
            fallback identity matcher.
        set_id: the Job's ``LoobricSetId`` property, when present.

    Returns ``{"set_id", "created", "name_conflict"}`` — on a name collision
    the set is still fully created/updated (members and provenance are not
    held hostage to the name); the caller re-prompts and calls
    :func:`claim_set_name`.
    """
    for member in plan["members"]:
        if member["needs_upload"]:
            member["tool_record_id"] = sync.upload_bit(
                tools_dir, client, member["path"], log=log)
            member["needs_upload"] = False

    all_sets = client.list_sets()
    sets_by_id = {_record_id(s): s for s in all_sets}
    rid = set_id if set_id in sets_by_id else None
    if rid is None:
        # The Job property is gone or stale — fall back to the provenance
        # stamp before creating, so a re-run never mints a sibling set.
        for record in all_sets:
            data = ((record.get("clients") or {}).get(mapping.CLIENT_NAME)
                    or {}).get("data") or {}
            if (data.get("origin") == "job"
                    and data.get("document") == provenance.get("document")
                    and data.get("job") == provenance.get("job")):
                rid = _record_id(record)
                break

    data = {"origin": "job", "document": provenance.get("document"),
            "job": provenance.get("job")}
    client_item_id = "job:%s#%s" % (provenance.get("document"),
                                    provenance.get("job"))
    created = rid is None
    if created:
        rid = _record_id(client.create_set(data=data,
                                           client_item_id=client_item_id))
        log("CREATE tool set from job '%s' (record %s)"
            % (provenance.get("job"), rid[:8]))
    else:
        client.put_set_section(rid, data, client_item_id)
        log("UPDATE tool set from job '%s' (record %s)"
            % (provenance.get("job"), rid[:8]))

    client.set_members(
        rid, [{"tool_record_id": m["tool_record_id"], "number": m["number"]}
              for m in plan["members"]],
        actor=mapping.CLIENT_NAME)
    log("  %d member claim(s) set" % len(plan["members"]))

    name_conflict = not claim_set_name(client, rid, name)
    if name_conflict:
        log("  ⚠ another set is already named '%s' — choose a different name"
            % name)
    return {"set_id": rid, "created": created, "name_conflict": name_conflict}
