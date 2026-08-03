# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""Tests for the mirror sync-ownership lock (mirrorlock).

One FreeCAD instance owns a mirror's reconciliation; a second acquire on the
same mirror fails while the owner is alive — and succeeds (the steal path)
once the owner is a dead pid or its heartbeat has gone stale.
"""
import json
import os
import time

from freecad.Loobric import mirrorlock


def test_acquire_release_roundtrip(tmp_path):
    lock = mirrorlock.MirrorLock(tmp_path)
    assert lock.acquire() is True
    holder, mtime = lock.holder()
    assert holder["pid"] == os.getpid() and mtime > 0
    lock.release()
    assert lock.holder() == (None, 0)


def test_second_acquire_rides_along(tmp_path):
    """A live owner keeps the lock; the second instance must not sync."""
    first = mirrorlock.MirrorLock(tmp_path)
    assert first.acquire()
    second = mirrorlock.MirrorLock(tmp_path)
    assert second.acquire() is False
    first.release()


def test_steal_from_dead_pid(tmp_path):
    """Same host, pid gone: the lock is a corpse and is stolen immediately."""
    path = tmp_path / mirrorlock.LOCK_NAME
    path.write_text(json.dumps({
        "pid": 2 ** 22 + 12345,          # vanishingly unlikely to exist
        "host": __import__("socket").gethostname(),
        "started": time.time()}))
    lock = mirrorlock.MirrorLock(tmp_path)
    assert lock.acquire() is True
    holder, _ = lock.holder()
    assert holder["pid"] == os.getpid()


def test_steal_from_stale_heartbeat_other_host(tmp_path):
    """Other host (pid undecidable): only a stale heartbeat allows the steal."""
    path = tmp_path / mirrorlock.LOCK_NAME
    path.write_text(json.dumps({
        "pid": 1, "host": "some-other-machine", "started": 0}))
    fresh = time.time()
    os.utime(path, (fresh, fresh))
    assert mirrorlock.MirrorLock(tmp_path).acquire() is False   # looks alive

    old = fresh - mirrorlock.STALE_AFTER - 60
    os.utime(path, (old, old))
    assert mirrorlock.MirrorLock(tmp_path).acquire() is True    # stale → steal


def test_garbage_lock_is_stolen(tmp_path):
    (tmp_path / mirrorlock.LOCK_NAME).write_text("not json at all")
    assert mirrorlock.MirrorLock(tmp_path).acquire() is True


def test_heartbeat_refreshes_mtime(tmp_path):
    lock = mirrorlock.MirrorLock(tmp_path)
    lock.acquire()
    old = time.time() - 3600
    os.utime(lock.path, (old, old))
    lock.heartbeat()
    assert os.path.getmtime(lock.path) > old + 1800
    lock.release()
