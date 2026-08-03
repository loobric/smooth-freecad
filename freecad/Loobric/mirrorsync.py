# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""Unattended sync policy for the Loobric asset store.

The asset store serves FreeCAD's toolbits and libraries out of a *mirror*
directory — an ordinary FreeCAD tools dir (``Bit/`` + ``Library/``) that this
module keeps reconciled with the server using the existing plan/apply engine.
What is new here is only the POLICY: which plan actions a refresh may apply
without a human in the loop.

The rule (0.6.0): a refresh moves data in whichever direction is unambiguous —
server-only -> pull, local-only -> push, changed on exactly one side -> that
side wins, and a DELETION on either side propagates to the other (the mirror
keeps a timestamped copy in ``.trash/`` first, with retention). Only a
CONFLICT — changed on both sides — is held and reported, never applied
silently. Held items are exactly what the Sync window exists to resolve.

Read-only keys: a refresh with ``read_only=True`` applies only the pull
directions. Push-shaped work should never exist (the store fails writes fast
on a read-only key), but anything that slipped into the mirror anyway (an
external edit, a key downgraded after the fact) is reported in
``summary["ro_blocked"]`` instead of dying on a 403 mid-apply.

Mirrors are PER SERVER: :func:`server_slug` keys a mirror directory to the
server URL, so switching servers can never cross-pollinate tool data.

No FreeCAD imports; runs headless under pytest.
"""

import hashlib
import os
import re
import shutil
import time
from urllib.parse import urlparse

from . import sync

# plan action -> the safe unattended decision
SAFE_DECISIONS = {
    "new_server": "pull",      # exists only on the server: materialize it
    "pull": "pull",            # changed only on the server: take it
    "new_local": "push",       # created here (e.g. through the asset store): upload
    "push": "push",            # changed only here: upload
    "deleted_local": "push",   # deleted here: delete the server record too
    "deleted_server": "pull",  # deleted on the server: remove it here (via trash)
}

# actions that need a human; a refresh reports these untouched
HELD_ACTIONS = ("conflict",)

# directions a read-only key may take
PULL_DECISIONS = ("pull",)

TRASH_DIR = ".trash"
TRASH_RETENTION_DAYS = 30

# The mirror's tool-file layout — the ONE definition both sides share: the
# asset store maps URIs through it, and this module reconciles the same
# dirs. 0.6.0 had two definitions (the store copied CAM's user-store
# mapping, which nests under ``Tools/``), splitting the mirror into a tree
# sync pushed/pulled and a different tree FreeCAD read/wrote.
MIRROR_MAPPING = {
    "toolbit": "Bit/{asset_id}.fctb",
    "toolbitlibrary": "Library/{asset_id}.fctl",
}

# top-level mirror dirs holding tool files (derived from MIRROR_MAPPING)
MIRROR_SUBDIRS = tuple(sorted({p.split("/", 1)[0] for p in MIRROR_MAPPING.values()}))


def server_slug(base_url):
    """A filesystem-safe, per-server mirror key: host[-port] + a short hash.

    The readable part is for humans poking around ``freecad_mirror/``; the
    hash disambiguates schemes, paths, and anything the sanitizer collapsed.
    """
    url = (base_url or "").strip().rstrip("/")
    parsed = urlparse(url if "://" in url else "https://" + url)
    host = parsed.netloc or "server"
    readable = re.sub(r"[^A-Za-z0-9.-]+", "-", host).strip("-.") or "server"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return "%s-%s" % (readable, digest)


def mirror_root(base_root, base_url):
    """The per-server mirror tools dir under ``base_root``, created on
    demand with its ``Bit/`` and ``Library/`` subdirs."""
    root = os.path.join(str(base_root), server_slug(base_url))
    os.makedirs(os.path.join(root, "Bit"), exist_ok=True)
    os.makedirs(os.path.join(root, "Library"), exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# Trash: every file the policy removes is recoverable for a while
# ---------------------------------------------------------------------------

def trash_file(mirror_dir, path, now=None):
    """Copy ``path`` into the mirror's trash before it is deleted.

    Layout: ``.trash/<epoch-batch>/<subdir>/<basename>`` where ``subdir`` is
    the file's dir relative to the mirror (``Bit`` / ``Library``). Returns
    the trash path, or None if there was nothing to copy.
    """
    if not path or not os.path.exists(path):
        return None
    now = int(now if now is not None else time.time())
    rel = os.path.relpath(os.path.dirname(os.path.abspath(path)),
                          os.path.abspath(str(mirror_dir)))
    sub = "" if rel == "." or rel.startswith("..") else rel
    dest_dir = os.path.join(str(mirror_dir), TRASH_DIR, str(now), sub)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(path))
    shutil.copy2(path, dest)
    return dest


def prune_trash(mirror_dir, retention_days=TRASH_RETENTION_DAYS, now=None):
    """Drop trash batches older than the retention window. Returns the number
    of batches removed. Unparseable batch names are left alone."""
    trash = os.path.join(str(mirror_dir), TRASH_DIR)
    if not os.path.isdir(trash):
        return 0
    now = now if now is not None else time.time()
    cutoff = now - retention_days * 86400
    removed = 0
    for entry in os.listdir(trash):
        try:
            stamp = int(entry)
        except ValueError:
            continue
        if stamp < cutoff:
            shutil.rmtree(os.path.join(trash, entry), ignore_errors=True)
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# 0.6.0 mapping-defect migration
# ---------------------------------------------------------------------------

def _same_content(a, b):
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def _rmdir_if_empty(path):
    try:
        os.rmdir(path)
    except OSError:
        pass


def migrate_tools_subtree(mirror_dir, log=lambda msg: None, now=None):
    """Fold a stray ``Tools/`` subtree into the mirror's real tree.

    The 0.6.0 asset store copied CAM's user-store path mapping, which nests
    tool files under ``Tools/Bit`` and ``Tools/Library`` — while this module
    reconciles ``Bit/`` and ``Library/``. Every write FreeCAD made through
    the store landed in the ``Tools/`` tree (never pushed), and every read
    missed what the server sent. 0.6.1 pins the store to
    :data:`MIRROR_MAPPING`; this migrates mirrors the defect already
    touched, per file:

    - no counterpart in the real tree -> MOVE it in (the next refresh
      pushes it to the server);
    - identical content on both sides -> drop the duplicate;
    - both exist and differ -> the real (server-synced) tree wins; the
      ``Tools/`` copy goes to the trash, recoverable for the retention
      window.

    Unrecognized files under ``Tools/`` are left where they are (and keep
    the dir alive). Idempotent, cheap when there is no ``Tools/`` tree.
    Returns the number of files moved into the real tree.
    """
    tools_root = os.path.join(str(mirror_dir), "Tools")
    if not os.path.isdir(tools_root):
        return 0
    moved = 0
    for sub in MIRROR_SUBDIRS:
        src_dir = os.path.join(tools_root, sub)
        if not os.path.isdir(src_dir):
            continue
        dest_dir = os.path.join(str(mirror_dir), sub)
        os.makedirs(dest_dir, exist_ok=True)
        for name in sorted(os.listdir(src_dir)):
            src = os.path.join(src_dir, name)
            if not os.path.isfile(src):
                continue
            dest = os.path.join(dest_dir, name)
            if not os.path.exists(dest):
                shutil.move(src, dest)
                moved += 1
                log("recovered '%s/%s' from the mirror's stray Tools/ tree; "
                    "it uploads on the next refresh" % (sub, name))
            elif _same_content(src, dest):
                os.remove(src)
            else:
                trash = trash_file(mirror_dir, src, now=now)
                os.remove(src)
                log("'%s/%s' existed in both the stray Tools/ tree and the "
                    "synced tree with different content — kept the synced "
                    "copy; the other is in the trash (%s)" % (sub, name, trash))
        _rmdir_if_empty(src_dir)
    _rmdir_if_empty(tools_root)
    return moved


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

def auto_decisions(plan, read_only=False):
    """Split a plan into unattended decisions, held items, and (read-only
    mode only) blocked push-shaped items.

    Returns ``(decisions, held, ro_blocked)`` where ``decisions`` feeds
    :func:`sync.apply_sync`, ``held`` is the list of plan items a human still
    has to look at (the Sync window), and ``ro_blocked`` is push-shaped work
    a read-only key cannot perform.
    """
    decisions = {}
    held = []
    ro_blocked = []
    for item in plan["items"]:
        action = item["action"]
        decision = SAFE_DECISIONS.get(action)
        if decision:
            if read_only and decision not in PULL_DECISIONS:
                ro_blocked.append(item)
            else:
                decisions[item["key"]] = decision
        elif action in HELD_ACTIONS:
            held.append(item)
        # unchanged / note / job_set: nothing to do
    return decisions, held, ro_blocked


def describe_held(held):
    """One human-readable line per held item, for logs and dialogs."""
    reasons = {
        "conflict": "changed both here and on the server",
    }
    return ["%s '%s' — %s" % (item["kind"], item["name"],
                              reasons.get(item["action"], item["action"]))
            for item in held]


def missing_shapes(mirror_dir, available, log=lambda msg: None):
    """Custom shape files the mirror's bits reference but FreeCAD can't find.

    ``available`` is the set of shape filenames the CAM asset system can
    resolve (the user's local Shape/ dir + the builtin store). Shape file
    CONTENTS are not synced (TECHNICAL.md) — this warning is how a pulled
    bit's missing custom shape surfaces instead of a broken editor. Returns
    a sorted list of ``(shape_file, [bit basenames])``.
    """
    import json
    available = {os.path.basename(a) for a in (available or ())}
    wanted = {}
    bit_dir = os.path.join(str(mirror_dir), "Bit")
    if os.path.isdir(bit_dir):
        for name in sorted(os.listdir(bit_dir)):
            if not name.endswith(".fctb"):
                continue
            try:
                with open(os.path.join(bit_dir, name)) as f:
                    shape = os.path.basename(json.load(f).get("shape") or "")
            except (OSError, ValueError):
                continue
            if shape and shape not in available:
                wanted.setdefault(shape, []).append(name)
    result = sorted(wanted.items())
    for shape, bits in result:
        log("shape '%s' is not on this machine (needed by %s) — the tool "
            "opens once the shape file is added locally; shape sync is a "
            "planned follow-up" % (shape, ", ".join(bits)))
    return result


# ---------------------------------------------------------------------------
# One reconciliation pass
# ---------------------------------------------------------------------------

def refresh_mirror(mirror_dir, client, log=lambda msg: None, read_only=False,
                   retention_days=TRASH_RETENTION_DAYS):
    """One reconciliation pass: plan, trash-protect, apply, prune, report.

    Returns apply_sync's summary extended with:
        held:        plan items skipped because they need a human (conflicts)
        ro_blocked:  push-shaped items a read-only key cannot perform
        plan_errors: unreadable files etc. from planning
    """
    plan = sync.plan_sync(str(mirror_dir), client, log=log)
    decisions, held, ro_blocked = auto_decisions(plan, read_only=read_only)
    # every file the policy is about to remove goes to the trash first
    for item in plan["items"]:
        if (item["action"] == "deleted_server"
                and decisions.get(item["key"]) == "pull"):
            trash_file(mirror_dir, item.get("path"))
    summary = sync.apply_sync(str(mirror_dir), client, plan, decisions,
                              log=log)
    summary["held"] = held
    summary["ro_blocked"] = ro_blocked
    summary["plan_errors"] = plan["errors"]
    prune_trash(mirror_dir, retention_days=retention_days)
    for line in describe_held(held):
        log("held for the Sync window: %s" % line)
    for item in ro_blocked:
        log("read-only key: cannot upload %s '%s' — it stays in the mirror"
            % (item["kind"], item["name"]))
    return summary
