# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""Store-mode orchestration: activation, the opt-in preference, the one-time
import review.

Two entry points activate the asset store:

- the ``Loobric_AssetStore`` toggle command (manual, interactive), and
- :func:`auto_activate`, wired to the main window's ``workbenchActivated``
  signal — when the CAM workbench comes up and the preference is on, store
  mode starts by itself.

Both funnel through :func:`activate_store`, which runs camassets setup FIRST
(so the stock library seeding can never be mistaken for user tools and
pushed) and then activates the store WITHOUT blocking the GUI: the slow half
(server probe, lock, initial reconcile — a full library download on a
first-ever run) runs on a background thread via ``assetstore.prepare()``,
while the toolbar badge and status widget show ``activating``. Completion
trampolines back to the GUI thread (a queued Qt signal) for the store swap
(``assetstore.complete()``), the one-time import review of the user's
existing local tools (the ordinary 0.5.0 Sync checkbox UI, pointed at the
REAL local directory), and — from the interactive toggle command — the
summary dialog.

FreeCAD < 1.1 has no asset system: every entry point degrades to a clear
message (command) or a silent skip (auto), and the classic Sync window
remains the way to work.
"""

import os
import threading

import FreeCAD as App
from PySide import QtCore, QtGui

IMPORT_REVIEW_MARKER = ".import_review_done"


def _log(msg):
    App.Console.PrintMessage("Loobric: %s\n" % msg)


class _GuiTrampoline(QtCore.QObject):
    """Queued-signal bridge: the activation thread hands callables here and
    they run on the GUI thread (the store swap, dialogs, widget updates)."""
    fire = QtCore.Signal(object)

    def __init__(self):
        super().__init__()
        self.fire.connect(self._run, QtCore.Qt.QueuedConnection)

    @staticmethod
    def _run(fn):
        try:
            fn()
        except Exception as e:               # noqa: BLE001 — keep the loop alive
            App.Console.PrintError("Loobric: %s\n" % e)


_trampoline = None


def _ensure_trampoline():
    """Create the bridge ON THE GUI THREAD (a QObject lives in the thread
    that made it; the worker only ever emits)."""
    global _trampoline
    if _trampoline is None:
        _trampoline = _GuiTrampoline()
    return _trampoline


def _gui(fn):
    _trampoline.fire.emit(fn)


def store_available():
    """FreeCAD 1.1+ with the CAM asset system importable."""
    try:
        try:
            from freecad.Loobric import assetstore  # noqa: F401
        except ImportError:
            import assetstore                        # noqa: F401
        return True
    except ImportError:
        return False


def enabled_in_config():
    try:
        from freecad.Loobric import LoobricDialog
    except ImportError:
        import LoobricDialog
    return bool(LoobricDialog.LoobricConfig.load().get("asset_store"))


def _local_tool_count(tools_dir):
    count = 0
    for sub, ext in (("Bit", ".fctb"), ("Library", ".fctl")):
        d = os.path.join(str(tools_dir), sub)
        if os.path.isdir(d):
            count += sum(1 for n in os.listdir(d) if n.endswith(ext))
    return count


def _offer_import_review(mirror):
    """One-time, per mirror: existing local tools deserve a decision, not a
    silent bulk upload. Opens the ordinary Sync window pointed at the REAL
    local tools dir; the marker is written whatever the user chooses."""
    try:
        from freecad.Loobric import LoobricDialog
    except ImportError:
        import LoobricDialog
    marker = os.path.join(str(mirror), IMPORT_REVIEW_MARKER)
    if os.path.exists(marker):
        return
    with open(marker, "w") as f:
        f.write("shown once; delete this file to be offered the review again\n")
    local_dir = LoobricDialog.get_tools_dir()
    count = _local_tool_count(local_dir)
    if not count:
        return
    answer = QtGui.QMessageBox.question(
        None, "Loobric",
        "You have %d tool file(s) in FreeCAD's own tool directory that may "
        "not be in Loobric yet.\n\nReview them now and choose which to "
        "import? (The Sync window opens against that directory — check what "
        "you want uploaded, then Apply.)" % count,
        QtGui.QMessageBox.Yes | QtGui.QMessageBox.No)
    if answer != QtGui.QMessageBox.Yes:
        _log("import review skipped — run the Sync window against %s "
             "any time" % local_dir)
        return
    window = LoobricDialog.LoobricWindow(tools_dir=str(local_dir))
    _offer_import_review._window = window            # keep alive (modeless)
    window.setModal(False)
    window.show()
    window.raise_()


def _summary_text(summary):
    lines = ["FreeCAD's tool library is now served from Loobric.",
             "Sync runs in the background — the status-bar widget shows "
             "its state."]
    if isinstance(summary, dict):
        lines += ["",
                  "Initial reconcile: %d downloaded, %d uploaded, "
                  "%d error(s)."
                  % (summary["pulled"], summary["pushed"],
                     len(summary["errors"]) + len(summary["plan_errors"]))]
        try:
            from freecad.Loobric import mirrorsync
        except ImportError:
            import mirrorsync
        held = mirrorsync.describe_held(summary["held"])
        if held:
            lines += ["", "Needs a decision in the Sync window:"]
            lines += ["  • %s" % h for h in held]
        for err in summary["errors"] + summary["plan_errors"]:
            lines.append("  ! %s" % err)
    lines += ["", "Reopen the tool library dock to see the refreshed "
                  "content. Run the asset-store command again to switch "
                  "back."]
    return "\n".join(lines)


def _activation_done(prepared, interactive):
    """GUI thread: swap the store in, then the one-time review + summary."""
    try:
        from freecad.Loobric import assetstore
    except ImportError:
        import assetstore
    summary = assetstore.complete(prepared, log=_log)
    _offer_import_review(assetstore.active_tools_dir())
    if interactive:
        QtGui.QMessageBox.information(None, "Loobric", _summary_text(summary))


def _activation_failed(error, interactive):
    """GUI thread: back to inactive, with the failure surfaced per mode."""
    try:
        from freecad.Loobric import assetstore
        from freecad.Loobric.client import LoobricError
    except ImportError:
        import assetstore
        from client import LoobricError
    assetstore.abort_activation()
    if not interactive:
        _log("asset store not started: %s" % error)
        return
    if isinstance(error, LoobricError):
        QtGui.QMessageBox.warning(
            None, "Loobric",
            "Cannot activate the Loobric asset store — the first fill "
            "of the mirror needs the server:\n\n%s" % error)
    else:
        QtGui.QMessageBox.warning(
            None, "Loobric",
            "Loobric asset-store activation failed:\n\n%s" % error)


def activate_store(interactive=False):
    """Start store mode. Returns True when activation is underway (or the
    store is already active/activating), False when it cannot start at all.

    The slow half runs on a background thread so the CAM UI never blocks —
    worst on a first run, when the whole library downloads. Completion
    feedback arrives from the GUI-thread callback: dialogs when
    interactive=True (the toggle command), console only when False
    (auto-activation — a failed auto-start must never block FreeCAD).
    """
    try:
        from freecad.Loobric import LoobricDialog, LoobricStatusWidget, assetstore
        from freecad.Loobric.client import LoobricApi
    except ImportError as e:
        if interactive:
            QtGui.QMessageBox.warning(
                None, "Loobric",
                "The Loobric asset store needs FreeCAD 1.1's CAM asset "
                "system, which this FreeCAD does not provide.\n\n(%s)" % e)
        return False

    if assetstore.is_active():
        return True
    if not assetstore.begin_activation():
        if interactive:
            QtGui.QMessageBox.information(
                None, "Loobric",
                "The Loobric asset store is already starting — the status "
                "bar shows its progress.")
        return True

    # camassets setup FIRST: an empty local store gets its stock seeding
    # done before the swap, so it can never be pushed to the server. Local
    # disk only — cheap enough to stay on the GUI thread.
    from Path.Tool import camassets
    try:
        camassets.ensure_assets_initialized()
    except Exception as e:                           # noqa: BLE001
        _log("camassets setup: %s (continuing)" % e)

    config = LoobricDialog.LoobricConfig.load()
    if not config.get("api_key"):
        assetstore.abort_activation()
        if interactive:
            QtGui.QMessageBox.warning(
                None, "Loobric",
                "No API key is configured — set the server URL and key in "
                "Edit → Preferences → CAM → Loobric first.")
        else:
            _log("asset store not started: no API key configured")
        return False
    client = LoobricApi(config["api_url"], config.get("api_key", ""))

    # the widget shows "starting…" from the first moment; a failure lands
    # back on the inactive state, which hides it again
    LoobricStatusWidget.install()
    _ensure_trampoline()

    def work():
        try:
            prepared = assetstore.prepare(client, log=_log)
        except Exception as e:                       # noqa: BLE001
            # rebind: `e` is unbound when the except block exits, and the
            # lambda runs later, on the GUI thread
            error = e
            _gui(lambda: _activation_failed(error, interactive))
        else:
            _gui(lambda: _activation_done(prepared, interactive))

    threading.Thread(target=work, daemon=True,
                     name="loobric-activate").start()
    return True


def deactivate_store():
    try:
        from freecad.Loobric import LoobricStatusWidget, assetstore
    except ImportError:
        import LoobricStatusWidget
        import assetstore
    assetstore.deactivate(log=_log)
    LoobricStatusWidget.remove()


def auto_activate(workbench_name):
    """workbenchActivated hook: start store mode when CAM comes up and the
    preference is on. Never raises."""
    try:
        if "CAM" not in str(workbench_name):
            return
        if not store_available() or not enabled_in_config():
            return
        try:
            from freecad.Loobric import assetstore
        except ImportError:
            import assetstore
        if assetstore.is_active():
            return
        activate_store(interactive=False)
    except Exception as e:                           # noqa: BLE001
        App.Console.PrintError("Loobric: asset-store auto-start failed: "
                               "%s\n" % e)
