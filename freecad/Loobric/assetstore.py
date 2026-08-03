# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""Serve FreeCAD's tool library from Loobric (experimental, 0.6.0).

FreeCAD 1.1's CAM asset system resolves every toolbit, library, shape, and
machine through named ``AssetStore`` backends; the stock ``"local"`` store is
a ``FileStore`` over the user's asset directory. This module swaps in a
:class:`LoobricAssetStore` under the same name — a public-API store swap via
``cam_assets.register_store()``, no monkeypatching — so the NATIVE tool
browsers, selectors, and editors transparently show and edit server-backed
content.

How it works:

- Toolbits and libraries are served from a PER-SERVER *mirror* directory
  (``~/.config/loobric/freecad_mirror/<server-slug>/``), an ordinary
  ``Bit/`` + ``Library/`` tools dir reconciled with the server by
  :mod:`mirrorsync`. Reads never touch the network.
- A write through the store lands in the mirror first and pokes the ONE
  background worker (:mod:`syncworker`), which debounces pushes, pulls
  periodically, and honors manual refreshes — store calls never do HTTP on
  the GUI thread.
- Deletions PROPAGATE (both ways); the mirror trashes every file first
  (``.trash/``, 30-day retention). Only conflicts are held for the Sync
  window.
- One FreeCAD instance owns sync per mirror (:mod:`mirrorlock`); others ride
  along: they read and write the mirror, and the owner's next pass
  reconciles their changes.
- A read-only key (server-side door scopes) puts the store in READ-ONLY
  mode: pulls work, writes fail fast at the edit with a clear message —
  never silently landing unpushable in the mirror.
- Every other asset type — shapes, their icons, and ``machine`` (.fcm)
  assets — passes through to the original local FileStore untouched.
  ``.fcm`` pass-through is PERMANENT policy: a FreeCAD machine config
  (post/kinematics) is a different concept from a Loobric Machine and the
  store must never touch it.
"""

import pathlib

from Path.Tool import camassets
from Path.Tool.assets import FileStore

try:
    from freecad.Loobric import mirrorlock, mirrorsync, syncworker
    from freecad.Loobric.client import LoobricError
except ImportError:                      # flat/dev layout
    import mirrorlock
    import mirrorsync
    import syncworker
    from client import LoobricError

# Asset types Loobric owns; everything else passes through to the real
# local store. Shapes stay local because custom shape .fcstd contents are
# not synced yet (mirrorsync.missing_shapes is the warning); machine (.fcm)
# pass-through is permanent policy.
TOOL_ASSET_TYPES = frozenset({"toolbit", "toolbitlibrary"})

READ_ONLY_MESSAGE = (
    "This Loobric API key is read-only — the tool library is browseable but "
    "not editable. Create a key with the 'sync' and 'assert' scopes to edit "
    "tools here.")


def mirror_base() -> pathlib.Path:
    return pathlib.Path.home() / ".config" / "loobric" / "freecad_mirror"


def mirror_dir(base_url) -> pathlib.Path:
    """The per-server mirror tools dir (created on demand)."""
    return pathlib.Path(mirrorsync.mirror_root(mirror_base(), base_url))


class LoobricAssetStore(FileStore):
    """A ``"local"``-named store: tool assets from the Loobric mirror,
    everything else delegated to the wrapped original FileStore."""

    def __init__(self, base_dir, fallback, client, log=lambda msg: None,
                 read_only=False, notify=lambda: None):
        # The mapping is the mirror's OWN layout (the one mirrorsync
        # reconciles), never the fallback's: 0.6.0 copied
        # ``fallback._mapping``, which nests under ``Tools/`` — so FreeCAD
        # read/wrote a tree sync never touched (writes stranded unpushed,
        # server tools invisible). mirrorsync.migrate_tools_subtree()
        # repairs mirrors that defect already touched.
        super().__init__(name=fallback.name, base_dir=base_dir,
                         mapping=dict(mirrorsync.MIRROR_MAPPING))
        self._fallback = fallback
        self._client = client
        self._log = log
        self.read_only = read_only
        self._notify = notify            # pokes the sync worker after a write

    # -- reconciliation ----------------------------------------------------

    def reconcile(self):
        """One mirror<->server pass; called by the worker (or synchronously
        during activation). Returns the summary, or None when the server was
        unreachable (mirror still authoritative locally; nothing lost)."""
        try:
            return mirrorsync.refresh_mirror(self._base_dir, self._client,
                                             log=self._log,
                                             read_only=self.read_only)
        except (LoobricError, OSError) as e:
            self._log("server not reachable (%s) — working from the mirror; "
                      "changes upload on the next refresh" % e)
            return None

    # -- reads ---------------------------------------------------------------

    async def get(self, uri):
        if uri.asset_type in TOOL_ASSET_TYPES:
            return await FileStore.get(self, uri)
        return await self._fallback.get(uri)

    async def exists(self, uri):
        if uri.asset_type in TOOL_ASSET_TYPES:
            return await FileStore.exists(self, uri)
        return await self._fallback.exists(uri)

    async def list_versions(self, uri):
        if uri.asset_type in TOOL_ASSET_TYPES:
            return await FileStore.list_versions(self, uri)
        return await self._fallback.list_versions(uri)

    async def list_assets(self, asset_type=None, limit=None, offset=None):
        if asset_type in TOOL_ASSET_TYPES:
            return await FileStore.list_assets(self, asset_type, limit, offset)
        if asset_type is not None:
            return await self._fallback.list_assets(asset_type, limit, offset)
        # untyped listing: tool assets from the mirror + the rest from the
        # fallback (minus its tool assets, which the mirror supersedes)
        mine = await FileStore.list_assets(self)
        theirs = await self._fallback.list_assets()
        merged = mine + [u for u in theirs
                         if u.asset_type not in TOOL_ASSET_TYPES]
        start = offset or 0
        end = start + limit if limit is not None else None
        return merged[start:end]

    async def count_assets(self, asset_type=None):
        return len(await self.list_assets(asset_type))

    async def is_empty(self, asset_type=None):
        # Tool asset types NEVER report empty: camassets' ensure_* helpers
        # seed the stock library into any store whose is_empty() answers
        # True — and they re-run from UI entry points long after
        # activation, so activation ordering cannot prevent it. Anything
        # seeded into the mirror would be pushed to the server as if the
        # user created it. An empty Loobric account is intentionally empty.
        if asset_type is None or asset_type in TOOL_ASSET_TYPES:
            return False
        return await self._fallback.is_empty(asset_type)

    # -- writes (mirror first, then the worker pushes) ------------------------

    def _check_writable(self):
        if self.read_only:
            raise LoobricError(READ_ONLY_MESSAGE)

    async def create(self, asset_type, asset_id, data):
        if asset_type not in TOOL_ASSET_TYPES:
            return await self._fallback.create(asset_type, asset_id, data)
        self._check_writable()
        uri = await FileStore.create(self, asset_type, asset_id, data)
        self._notify()
        return uri

    async def update(self, uri, data):
        if uri.asset_type not in TOOL_ASSET_TYPES:
            return await self._fallback.update(uri, data)
        self._check_writable()
        result = await FileStore.update(self, uri, data)
        self._notify()
        return result

    async def delete(self, uri):
        if uri.asset_type not in TOOL_ASSET_TYPES:
            return await self._fallback.delete(uri)
        self._check_writable()
        # trash first — the deletion PROPAGATES to the server on the next
        # pass, and the trash copy (30-day retention) is the undo
        try:
            mirrorsync.trash_file(self._base_dir, str(self._uri_to_path(uri)))
        except ValueError:
            pass                         # unresolvable uri: nothing to trash
        await FileStore.delete(self, uri)
        self._notify()
        self._log("deleted '%s' — the server record will be deleted on the "
                  "next sync pass; a copy is in the mirror's trash" % uri)


# -- activation ---------------------------------------------------------------

_active_store = None
_worker = None
_lock = None
_activating = False
_state = {"status": "inactive", "held": 0, "read_only": False,
          "owns_sync": True, "last_summary": None}
_state_listeners = []


def is_active():
    return _active_store is not None


def active_tools_dir():
    """The mirror dir when the store is active, else None (the Sync tab uses
    this to retarget itself at whatever FreeCAD is actually showing)."""
    return str(_active_store._base_dir) if _active_store else None


def state():
    """A snapshot for status UI: status (inactive/activating/idle/syncing/
    offline), held count, read_only, owns_sync, last_summary."""
    return dict(_state)


def add_state_listener(callback):
    """Register callback(state_dict); called on every state change, possibly
    FROM THE WORKER THREAD — Qt consumers must trampoline to the GUI thread."""
    _state_listeners.append(callback)


def remove_state_listener(callback):
    if callback in _state_listeners:
        _state_listeners.remove(callback)


def _set_state(**changes):
    _state.update(changes)
    snapshot = dict(_state)
    for callback in list(_state_listeners):
        try:
            callback(snapshot)
        except Exception:                # noqa: BLE001 — UI must not kill sync
            pass


def _apply_summary(summary):
    if summary is None:
        _set_state(status="offline")
    else:
        _set_state(status="idle", held=len(summary.get("held", ())),
                   last_summary=summary)


def probe_read_only(client, log=lambda msg: None):
    """Whether the configured key is read-only (server >= 0.8.0 introspection).

    An older server (no /auth/key) or an unreachable one answers None —
    unknown, treated as writable; enforcement then still happens server-side."""
    try:
        info = client.key_info()
    except LoobricError:
        return None
    if info.get("channel") != "api-key":
        return False
    if info.get("legacy"):
        log("this API key predates door scopes and is READ-ONLY — create a "
            "new key (loobric create-key --preset cam) to edit tools")
    return bool(info.get("read_only"))


def available_shapes():
    """Shape filenames FreeCAD can resolve (user Shape/ dir + builtin store)."""
    names = set()
    for store in (camassets.user_asset_store, camassets.builtin_asset_store):
        try:
            shape_dir = store._base_dir / "Shape"
            names.update(p.name for p in shape_dir.glob("*.fcstd"))
        except (AttributeError, OSError):
            continue
    return names


def begin_activation():
    """Claim the activation slot and show ``activating`` to status UIs.

    Returns False when the store is already active or an activation is
    already in flight — exactly one activation runs at a time, however many
    workbench switches or toggle clicks race for it."""
    global _activating
    if _activating or _active_store is not None:
        return False
    _activating = True
    _set_state(status="activating")
    return True


def is_activating():
    return _activating


def abort_activation():
    """Activation failed or was abandoned: back to inactive (which also
    hides the status widget)."""
    global _activating
    if not _activating:
        return
    _activating = False
    if _active_store is None:
        _set_state(status="inactive", held=0, read_only=False,
                   owns_sync=True, last_summary=None)


def prepare(client, log=lambda msg: None):
    """The slow half of activation: key introspection, mirror migration,
    lock acquisition, and the initial reconcile (a full download on a
    first-ever run). Touches no module globals and no Qt — safe to run on a
    background thread while the GUI stays live (storemode does exactly
    that; the toolbar/status UI shows ``activating`` meanwhile).

    - Any stray ``Tools/`` subtree from the 0.6.0 mapping defect is folded
      back into the mirror first (stranded writes upload on the next
      refresh; see mirrorsync.migrate_tools_subtree).
    - Key introspection first: a read-only key prepares a read-only store.
    - The initial reconcile runs BEFORE the swap, so the store never serves
      an empty mirror on first use. Raises LoobricError if the server can't
      be reached for a first-ever fill (an already-populated mirror
      activates offline).

    Returns the dict :func:`complete` consumes."""
    read_only = probe_read_only(client, log=log)
    base = mirror_dir(client.base_url)
    mirrorsync.migrate_tools_subtree(base, log=log)
    lock = mirrorlock.MirrorLock(base)
    owns_sync = lock.acquire()
    if owns_sync:
        # a clean FreeCAD exit without deactivate() must not leave the next
        # instance waiting out the staleness window
        import atexit
        atexit.register(lock.release)

    store = LoobricAssetStore(base, camassets.user_asset_store, client,
                              log=log, read_only=bool(read_only))

    summary = None
    if owns_sync:
        summary = store.reconcile()
        if summary is None and not any(base.glob("Bit/*.fctb")):
            lock.release()
            raise LoobricError(
                "the first fill of the mirror needs the server, and it "
                "could not be reached")
    return {"store": store, "lock": lock, "owns_sync": owns_sync,
            "summary": summary}


def complete(prepared, log=lambda msg: None):
    """The fast half of activation: start the worker and swap the store in
    as ``"local"``. MUST run on the GUI thread — it changes what the native
    tool UI is reading through cam_assets.

    Call AFTER camassets setup has run (ensure_assets_initialized). Ordering
    alone is NOT the seeding safety net — camassets' ensure_* helpers re-run
    from UI entry points long after activation — the store's ``is_empty``
    override is (tool types never report empty, so the stock library is
    never seeded into the mirror and pushed).

    If another FreeCAD instance owns this mirror's sync, this one rides
    along: no worker, reads/writes still work.

    Returns the initial reconcile summary (None when riding along/offline).
    """
    global _active_store, _worker, _lock, _activating
    _activating = False
    store, lock = prepared["store"], prepared["lock"]
    if _active_store is not None:            # lost a race; keep the winner
        lock.release()
        return None
    owns_sync = prepared["owns_sync"]
    summary = prepared["summary"]

    if owns_sync:
        worker = syncworker.SyncWorker(
            store.reconcile,
            on_state=lambda s: _set_state(status=s) if s == "syncing" else None,
            on_result=_apply_summary,
            heartbeat=lock.heartbeat)
        store._notify = worker.notify_write
        worker.start()
        _worker = worker
    else:
        log("another FreeCAD instance is syncing this mirror — riding along "
            "(reads and writes work; it reconciles for both)")

    camassets.cam_assets.register_store(store)      # replaces "local"
    _active_store = store
    _lock = lock
    _set_state(status="idle", read_only=store.read_only, owns_sync=owns_sync,
               held=len(summary["held"]) if summary else 0,
               last_summary=summary)

    for _shape, _bits in mirrorsync.missing_shapes(
            store._base_dir, available_shapes(), log=log):
        pass                                        # logged by missing_shapes

    log("Loobric asset store active%s — FreeCAD's tool library is now "
        "served from the server mirror at %s"
        % (" (read-only key)" if store.read_only else "", store._base_dir))
    return summary


def activate(client, log=lambda msg: None):
    """Synchronous activation: prepare + complete in one call (blocks on the
    initial fill). storemode activates asynchronously through the same
    pieces; this remains for non-GUI callers and tests."""
    if not begin_activation():
        return None
    try:
        prepared = prepare(client, log=log)
    except Exception:
        abort_activation()
        raise
    return complete(prepared, log=log)


def deactivate(log=lambda msg: None):
    """Restore the original local FileStore. The mirror is left in place."""
    global _active_store, _worker, _lock
    if _active_store is None:
        return
    if _worker is not None:
        _worker.stop()
        _worker = None
    if _lock is not None:
        _lock.release()
        _lock = None
    camassets.cam_assets.register_store(camassets.user_asset_store)
    _active_store = None
    _set_state(status="inactive", held=0, read_only=False, owns_sync=True,
               last_summary=None)
    log("Loobric asset store deactivated — FreeCAD's own local tool "
        "directory is back")


def refresh(log=lambda msg: None):
    """Reconcile the active store's mirror with the server, via the worker
    when this instance owns sync (immediately, skipping the debounce)."""
    if _active_store is None:
        return None
    if _worker is not None:
        _worker.refresh_now()
        return None
    return _active_store.reconcile()
