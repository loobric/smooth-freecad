# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""One FreeCAD instance owns a mirror's sync; the others ride along.

Two FreeCAD windows on the same mirror must not both run reconciliation —
the plan/apply journal is not built for concurrent writers. The owner is
whoever holds ``sync.lock`` in the mirror directory; everyone else reads and
writes the mirror normally (the owner's next pass reconciles their changes)
but runs no sync of its own.

Staleness (a crashed owner must never wedge sync forever):

- the lock names its ``pid`` and ``host``; on the same host, a dead pid is
  stale immediately
- the owner heartbeats the lock (touches its mtime) on every worker wake;
  a heartbeat older than :data:`STALE_AFTER` seconds is stale regardless of
  host — that is the cross-host / undecidable-pid fallback

Acquisition is O_CREAT|O_EXCL (atomic on every platform FreeCAD runs on);
a stale lock is removed and acquisition retried exactly once, so two
starters racing over a corpse cannot both win.

No FreeCAD imports; runs headless under pytest.
"""

import json
import os
import socket
import time

LOCK_NAME = "sync.lock"

# The worker heartbeats every wake (<= 60 s apart); anything this old means
# the owner is gone, whatever host it was on.
STALE_AFTER = 15 * 60


def _pid_alive(pid):
    """Best effort 'is this pid running on THIS host'."""
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError):
        return True          # exists but isn't ours / unhandleable: assume alive
    except (OSError, ValueError, TypeError):
        return False


def is_stale(info, mtime, now=None, host=None):
    """Whether a lock with contents ``info`` and heartbeat ``mtime`` is dead."""
    now = now if now is not None else time.time()
    host = host or socket.gethostname()
    if now - mtime > STALE_AFTER:
        return True
    if info.get("host") == host and not _pid_alive(info.get("pid")):
        return True
    return False


class MirrorLock:
    """The sync-ownership lock for one mirror directory."""

    def __init__(self, mirror_dir):
        self.path = os.path.join(str(mirror_dir), LOCK_NAME)
        self.owned = False

    def acquire(self):
        """Try to become the mirror's sync owner. Returns True on success;
        False means another live FreeCAD owns sync (ride along)."""
        for attempt in (1, 2):        # second attempt only after a steal
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder, mtime = self.holder()
                if holder is None or is_stale(holder, mtime):
                    # a corpse (or garbage) — steal it and retry once
                    try:
                        os.remove(self.path)
                    except OSError:
                        return False
                    continue
                return False
            with os.fdopen(fd, "w") as f:
                json.dump({"pid": os.getpid(),
                           "host": socket.gethostname(),
                           "started": time.time()}, f)
            self.owned = True
            return True
        return False

    def holder(self):
        """The current lock contents and heartbeat mtime, or (None, 0)."""
        try:
            mtime = os.path.getmtime(self.path)
            with open(self.path) as f:
                return json.load(f), mtime
        except (OSError, ValueError):
            return None, 0

    def heartbeat(self):
        """Refresh the lock's mtime — call on every worker wake."""
        if self.owned:
            try:
                os.utime(self.path, None)
            except OSError:
                pass

    def release(self):
        if self.owned:
            self.owned = False
            try:
                os.remove(self.path)
            except OSError:
                pass
