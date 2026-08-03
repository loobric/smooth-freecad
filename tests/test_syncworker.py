# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""Tests for the background reconciliation worker (syncworker).

Real (short) intervals: the debounce coalesces a burst of writes into one
pass, the period fires with no writes at all, refresh_now is immediate, a
raising reconcile never kills the thread, and the heartbeat callback runs on
every wake (that is what keeps the mirror lock looking alive).
"""
import threading
import time

from freecad.Loobric.syncworker import SyncWorker


class Recorder:
    def __init__(self):
        self.runs = []
        self.states = []
        self.beats = 0
        self.ran = threading.Event()

    def reconcile(self):
        self.runs.append(time.monotonic())
        self.ran.set()
        return {"pulled": 0}

    def on_state(self, state):
        self.states.append(state)

    def heartbeat(self):
        self.beats += 1


def make(rec, **kw):
    kw.setdefault("debounce", 0.1)
    kw.setdefault("period", 30.0)
    return SyncWorker(rec.reconcile, on_state=rec.on_state,
                      heartbeat=rec.heartbeat, **kw)


def test_write_burst_coalesces_into_one_pass():
    rec = Recorder()
    w = make(rec)
    w.start()
    for _ in range(5):
        w.notify_write()
        time.sleep(0.01)
    assert rec.ran.wait(2)
    time.sleep(0.3)                      # long enough for a wrong second pass
    w.stop()
    assert len(rec.runs) == 1
    assert rec.states == ["syncing", "idle"]


def test_periodic_pass_fires_without_writes():
    rec = Recorder()
    w = make(rec, period=0.15)
    w.start()
    assert rec.ran.wait(2)
    w.stop()
    assert len(rec.runs) >= 1


def test_refresh_now_skips_the_debounce():
    rec = Recorder()
    w = make(rec, debounce=10.0, period=60.0)
    w.start()
    t0 = time.monotonic()
    w.refresh_now()
    assert rec.ran.wait(2)
    assert time.monotonic() - t0 < 5
    w.stop()


def test_reconcile_exception_does_not_kill_worker():
    results = []
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("server hiccup")
        return {"ok": True}

    w = SyncWorker(flaky, debounce=0.05, period=60.0,
                   on_result=results.append)
    w.start()
    w.refresh_now()
    time.sleep(0.3)
    w.notify_write()
    time.sleep(0.5)
    w.stop()
    assert results[0] is None            # the failure was reported, not fatal
    assert results[1] == {"ok": True}    # …and the worker lived to succeed


def test_heartbeat_runs_on_wakes():
    rec = Recorder()
    w = make(rec, period=60.0)
    w.start()
    w.refresh_now()
    assert rec.ran.wait(2)
    w.stop()
    assert rec.beats >= 1
