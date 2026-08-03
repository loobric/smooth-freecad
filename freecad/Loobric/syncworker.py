# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""The one background worker that owns all mirror reconciliation.

Store-mode doctrine: an asset-store read or write NEVER performs HTTP on the
calling (GUI) thread. Writes land in the mirror and poke this worker
(:meth:`SyncWorker.notify_write`); the worker coalesces pokes behind a short
debounce, runs a periodic pull so server-side changes arrive on their own,
and honors an immediate manual refresh. One reconcile runs at a time, always
on this thread.

The worker never sleeps more than :data:`MAX_NAP` seconds so it can heartbeat
the mirror lock (mirrorlock.py) often enough to look alive.

Callbacks (both optional, both called ON THE WORKER THREAD — Qt consumers
must trampoline to the GUI thread themselves):

- ``on_state(state)`` with ``"syncing"`` / ``"idle"`` around each pass
- ``on_result(summary_or_none)`` after each pass (None = the reconcile
  callable raised; the exception is swallowed after reporting, because a
  transient server error must never kill the worker)

Plain threading, no FreeCAD or Qt imports; runs headless under pytest.
"""

import threading

DEBOUNCE = 5.0          # seconds of quiet after a write before pushing
PERIOD = 600.0          # periodic pull interval
MAX_NAP = 60.0          # heartbeat cadence upper bound


class SyncWorker(threading.Thread):
    def __init__(self, reconcile, debounce=DEBOUNCE, period=PERIOD,
                 on_state=None, on_result=None, heartbeat=None,
                 clock=None):
        super().__init__(daemon=True, name="loobric-syncworker")
        self._reconcile = reconcile
        self._debounce = debounce
        self._period = period
        self._on_state = on_state or (lambda state: None)
        self._on_result = on_result or (lambda summary: None)
        self._heartbeat = heartbeat or (lambda: None)
        self._cond = threading.Condition()
        self._dirty_at = None        # monotonic time of the last write poke
        self._refresh_now = False
        self._stopped = False
        import time
        self._now = clock or time.monotonic
        self._last_run = self._now()

    # -- the API the store and UI call (any thread) -------------------------

    def notify_write(self):
        """A write landed in the mirror; reconcile after the debounce."""
        with self._cond:
            self._dirty_at = self._now()
            self._cond.notify()

    def refresh_now(self):
        """Reconcile as soon as possible (manual refresh)."""
        with self._cond:
            self._refresh_now = True
            self._cond.notify()

    def stop(self, join=True):
        with self._cond:
            self._stopped = True
            self._cond.notify()
        if join and self.is_alive():
            self.join(timeout=10)

    # -- the loop ------------------------------------------------------------

    def _due(self, now):
        if self._refresh_now:
            return True
        if self._dirty_at is not None and now - self._dirty_at >= self._debounce:
            return True
        return now - self._last_run >= self._period

    def _next_wait(self, now):
        candidates = [MAX_NAP, self._last_run + self._period - now]
        if self._dirty_at is not None:
            candidates.append(self._dirty_at + self._debounce - now)
        return max(0.05, min(candidates))

    def run(self):
        while True:
            with self._cond:
                while not self._stopped and not self._due(self._now()):
                    self._heartbeat()
                    self._cond.wait(self._next_wait(self._now()))
                if self._stopped:
                    return
                self._dirty_at = None
                self._refresh_now = False
            self._heartbeat()
            self._on_state("syncing")
            try:
                summary = self._reconcile()
            except Exception:            # noqa: BLE001 — the worker must survive
                summary = None
            self._last_run = self._now()
            self._on_state("idle")
            self._on_result(summary)
