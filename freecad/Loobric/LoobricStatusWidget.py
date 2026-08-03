# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""The asset-store status UI: the status-bar widget and the toolbar badge.

Store mode makes sync invisible — these two are where it stays visible:

- :class:`LoobricStatusWidget` (main-window status bar): starting… / idle /
  syncing… / "N held" (conflicts waiting in the Sync window), plus a
  read-only badge and a ride-along note. Clicking it opens the Loobric
  window; with held items, the Sync tab opens pre-filtered to what needs a
  decision.
- :class:`LoobricCommandBadge`: the Loobric toolbar button's icon itself is
  the indicator — the whole icon recolored green (in sync), yellow
  (activating or syncing), red (held conflicts need a decision), or gray
  (offline, working from the mirror); the normal brand color while store
  mode is off.

The asset store reports state changes from its WORKER thread; a queued Qt
signal trampolines them onto the GUI thread in both consumers.
"""

import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

try:
    from freecad.Loobric import assetstore, statuscolor
except ImportError:
    import assetstore
    import statuscolor


class LoobricStatusWidget(QtGui.QToolButton):
    _state_changed = QtCore.Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoRaise(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        # queued: emissions come from the sync worker thread
        self._state_changed.connect(self._apply,
                                    QtCore.Qt.QueuedConnection)
        self.clicked.connect(self._open_window)
        assetstore.add_state_listener(self._state_changed.emit)
        self._apply(assetstore.state())

    def detach(self):
        assetstore.remove_state_listener(self._state_changed.emit)

    # -- rendering -----------------------------------------------------------

    def _apply(self, state):
        status = state.get("status", "inactive")
        if status == "inactive":
            self.hide()
            return
        held = state.get("held", 0)
        if status == "activating":
            text = "Loobric: starting…"
        elif status == "syncing":
            text = "Loobric: syncing…"
        elif held:
            text = "Loobric: %d held" % held
        elif status == "offline":
            text = "Loobric: offline (mirror)"
        else:
            text = "Loobric: idle"
        badges = []
        if state.get("read_only"):
            badges.append("read-only")
        if not state.get("owns_sync", True):
            badges.append("ride-along")
        if badges:
            text += "  [%s]" % ", ".join(badges)
        self.setText(text)
        if status == "activating":
            tips = ["The Loobric asset store is starting: connecting to the "
                    "server and preparing the tool library mirror. FreeCAD "
                    "stays usable while this runs."]
        else:
            tips = ["The Loobric asset store is serving FreeCAD's tool "
                    "library."]
        if held:
            tips.append("%d item(s) need a decision — click to resolve in "
                        "the Sync window." % held)
        if state.get("read_only"):
            tips.append("This API key is read-only: tools are browseable, "
                        "not editable.")
        if not state.get("owns_sync", True):
            tips.append("Another FreeCAD instance runs the sync for this "
                        "mirror; this one rides along.")
        if status == "offline":
            tips.append("Server unreachable — working from the local "
                        "mirror; changes upload on the next successful pass.")
        self.setToolTip("\n".join(tips))
        self.show()

    # -- click: the Sync window, filtered when something is held --------------

    def _open_window(self):
        try:
            try:
                from freecad.Loobric import LoobricDialog
            except ImportError:
                import LoobricDialog
            window = LoobricDialog.LoobricWindow(
                focus_attention=assetstore.state().get("held", 0) > 0)
            LoobricStatusWidget._window = window     # keep alive (modeless)
            window.setModal(False)
            window.show()
            window.raise_()
            window.activateWindow()
        except Exception as e:                       # noqa: BLE001
            App.Console.PrintError("Loobric: cannot open the window: %s\n" % e)


_widget = None


def install():
    """Add the widget to the main window's status bar (idempotent)."""
    global _widget
    if _widget is not None:
        return _widget
    mw = Gui.getMainWindow()
    if mw is None:
        return None
    _widget = LoobricStatusWidget(mw)
    mw.statusBar().addPermanentWidget(_widget)
    return _widget


def remove():
    global _widget
    if _widget is None:
        return
    try:
        _widget.detach()
        mw = Gui.getMainWindow()
        if mw is not None:
            mw.statusBar().removeWidget(_widget)
        _widget.deleteLater()
    finally:
        _widget = None


# -- the toolbar-button status color -------------------------------------------


class LoobricCommandBadge(QtCore.QObject):
    """Recolors the ``Loobric_Sync`` toolbar/menu icon by sync status.

    The whole icon changes color (statuscolor.py has the mapping): the SVG
    source is tinted and re-rendered per color, so the shape stays crisp and
    no per-color icon files exist to maintain.

    FreeCAD keeps ONE QAction per command (toolbars and menus share it), so
    stamping the icon once covers every appearance — but the action only
    exists after some workbench first shows the command, so :meth:`refresh`
    re-stamps on every workbench switch.
    """
    _state_changed = QtCore.Signal(dict)

    COMMAND = "Loobric_Sync"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._icons = {}
        icon_path = os.path.join(
            os.path.dirname(__file__), "Resources", "icons", "Loobric.svg")
        self._base = QtGui.QIcon(icon_path)
        try:
            with open(icon_path) as f:
                self._svg = f.read()
        except OSError:
            self._svg = None                 # plain icon whatever the state
        # queued: emissions come from the sync worker thread
        self._state_changed.connect(self._apply, QtCore.Qt.QueuedConnection)
        assetstore.add_state_listener(self._state_changed.emit)
        self._last = assetstore.state()
        self._apply(self._last)

    def detach(self):
        assetstore.remove_state_listener(self._state_changed.emit)

    def refresh(self, *_args):
        """Re-stamp from the last known state (workbenchActivated hook)."""
        self._apply(self._last)

    def _icon(self, color):
        if color is None or self._svg is None:
            return self._base
        if color not in self._icons:
            pixmap = QtGui.QPixmap()
            tinted = statuscolor.tint_svg(self._svg, color).encode("utf-8")
            if pixmap.loadFromData(tinted, "SVG"):
                self._icons[color] = QtGui.QIcon(pixmap)
            else:
                self._icons[color] = self._base
        return self._icons[color]

    def _apply(self, state):
        self._last = state
        icon = self._icon(statuscolor.status_color(state))
        for action in self._actions():
            action.setIcon(icon)

    def _actions(self):
        try:
            action = Gui.Command.get(self.COMMAND).getAction()
        except Exception:                            # noqa: BLE001
            action = None
        if isinstance(action, list) and action:
            return action
        if action is not None and not isinstance(action, list):
            return [action]
        mw = Gui.getMainWindow()
        if mw is None:
            return []
        return mw.findChildren(QtGui.QAction, self.COMMAND)


_badge = None


def install_badge():
    """Attach the status coloring to the Loobric toolbar button (idempotent)."""
    global _badge
    if _badge is not None:
        return _badge
    mw = Gui.getMainWindow()
    if mw is None:
        return None
    _badge = LoobricCommandBadge(mw)
    # the command's QAction is created whenever a workbench first shows it —
    # re-stamp on every switch so the color survives toolbar (re)builds
    mw.workbenchActivated.connect(_badge.refresh)
    return _badge
