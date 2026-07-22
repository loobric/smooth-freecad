# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Loobric — the tabbed desktop window.

A deliberately small shell of three tabs over a single shared API client (so all
HTTP traffic lands in one call log): **Sync** (the one CAM surface — plan/apply
of the local tool directory, with an out-of-sync filter and per-set/-tool
management), **Machines** (the one binding surface — tool tables with the Inbox
folded in), and **Audit**. A header shows the server + connection state; raw
sectioned-record JSON inspection and a live API log are demoted behind a
**Debug** menu. The window is modeless so FreeCAD stays usable beside it.

All behavior lives in the headless-tested modules (client.py, sync.py,
mapping.py) and the pure view-model (viewmodel.py) consumed by LoobricTabs.py;
this file is the shell that wires them up.
"""
import json
from pathlib import Path
from typing import Dict

import FreeCAD as App
from PySide import QtGui, QtCore

from . import LoobricTabs
from .client import LoobricApi, LoobricError


class LoobricConfig:
    """Configuration in ~/.config/loobric/freecad.json (v1-compatible)."""

    @staticmethod
    def get_config_path() -> Path:
        config_dir = Path.home() / ".config" / "loobric"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "freecad.json"

    @staticmethod
    def load() -> Dict:
        default = {"api_url": "https://api.loobric.com", "api_key": ""}
        path = LoobricConfig.get_config_path()
        if path.exists():
            try:
                with open(path) as f:
                    default.update(json.load(f))
            except (OSError, ValueError) as e:
                App.Console.PrintWarning(f"Loobric: bad config file: {e}\n")
        url = default["api_url"].rstrip("/")
        if url.endswith("/api"):
            url = url[:-4]
        default["api_url"] = url
        return default

    @staticmethod
    def save(config: Dict) -> None:
        with open(LoobricConfig.get_config_path(), "w") as f:
            json.dump(config, f, indent=2)


def get_tools_dir() -> Path:
    """Locate FreeCAD's CAM tool assets directory."""
    try:
        from Path import Preferences
        asset_path = Path(Preferences.getAssetPath())
    except (ImportError, AttributeError):
        asset_path = Path(App.getUserAppDataDir()) / "Mod" / "Path"
    return asset_path / "Tools"


class LoobricWindow(QtGui.QDialog):
    """Tabbed Loobric window. Opens on the Sync tab; other tabs lazy-load when
    first shown. One client is shared by every tab. Modeless (see the command),
    so FreeCAD stays usable alongside it."""

    def __init__(self):
        super().__init__()
        self.config = LoobricConfig.load()
        self.client = LoobricApi(self.config["api_url"],
                                self.config.get("api_key", ""))
        self._api_panel = None
        self._build_ui()

    # -- UI ---------------------------------------------------------------

    def _build_ui(self):
        self.setWindowTitle("Loobric")
        self.resize(820, 620)
        layout = QtGui.QVBoxLayout(self)

        header = QtGui.QHBoxLayout()
        # A single Refresh control for the whole window — it reloads whichever
        # tab is showing (each tab fetches its own data).
        self.refresh_button = QtGui.QToolButton()
        try:    # standard reload icon; fall back to text if the enum isn't exposed
            self.refresh_button.setIcon(
                self.style().standardIcon(QtGui.QStyle.SP_BrowserReload))
        except Exception:
            self.refresh_button.setText("Refresh")
        self.refresh_button.setAutoRaise(True)
        self.refresh_button.setToolTip("Refresh the current tab from the server")
        self.refresh_button.clicked.connect(self.refresh_current_tab)
        header.addWidget(self.refresh_button)
        header.addStretch()
        # Connection state only — the server URL lives in its tooltip (it isn't
        # editable here). Plain text, no checkmark (it is not a control).
        self.conn_label = QtGui.QLabel("checking…")
        self.conn_label.setToolTip(f"Server: {self.config['api_url']}")
        header.addWidget(self.conn_label)
        layout.addLayout(header)

        self.tabs = QtGui.QTabWidget()
        tools_dir = str(get_tools_dir())
        # The one CAM surface; binding lives on Machines; Audit is read-only.
        self.sync_tab = LoobricTabs.SyncTab(self, self.client, tools_dir)
        self.catalog_tab = LoobricTabs.CatalogTab(self, self.client, tools_dir)
        self._tab_list = [
            self.sync_tab,
            self.catalog_tab,
            LoobricTabs.MachinesTab(self, self.client),
            LoobricTabs.AuditTab(self, self.client),
        ]
        for tab in self._tab_list:
            self.tabs.addTab(tab, tab.TITLE)
        self.tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self.tabs, stretch=1)

        self.status_label = QtGui.QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Diagnostics live behind a Debug menu, not in primary chrome. Record
        # inspection is a right-click action on any row (every tab), not a menu
        # item — so it works wherever a row is selected.
        bar = QtGui.QHBoxLayout()
        debug = QtGui.QPushButton("Debug ▾")
        debug_menu = QtGui.QMenu(debug)
        act_log = debug_menu.addAction("API log…")
        act_log.triggered.connect(self.show_api_log)
        debug.setMenu(debug_menu)
        bar.addWidget(debug)
        bar.addStretch()
        close = QtGui.QPushButton("Close")
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        layout.addLayout(bar)

    # -- lifecycle --------------------------------------------------------

    def showEvent(self, event):
        """Modeless open: kick the connection check + first load once shown."""
        super().showEvent(event)
        if not getattr(self, "_opened", False):
            self._opened = True
            QtCore.QTimer.singleShot(50, self._on_open)

    def exec_(self):
        # Retained for back-compat; the workbench command now opens modeless via
        # show(). showEvent drives the open either way.
        return super().exec_()

    def _on_open(self):
        self._check_connection()
        self.sync_tab.ensure_loaded()        # the default, primary tab

    def _check_connection(self):
        try:
            self.client.ping()
            self.conn_label.setText("connected")
        except LoobricError as e:
            self.conn_label.setText("not connected")
            self.status(f"Cannot reach server: {e}")
        self.refresh_api_log()

    def refresh_current_tab(self):
        """The single header Refresh: re-check the connection and reload the
        tab currently showing."""
        self._check_connection()
        widget = self.tabs.currentWidget()
        if hasattr(widget, "refresh"):
            widget.refresh()

    def _tab_changed(self, index):
        widget = self.tabs.widget(index)
        if hasattr(widget, "ensure_loaded"):
            widget.ensure_loaded()

    # -- services the tabs call ------------------------------------------

    def status(self, message):
        self.status_label.setText(message)
        App.Console.PrintMessage("Loobric: %s\n" % message)
        QtGui.QApplication.processEvents()

    def inspect_selected(self):
        widget = self.tabs.currentWidget()
        record = widget.selected_record() if hasattr(widget, "selected_record") else None
        if not isinstance(record, dict):
            self.status("Select a row to inspect its record.")
            return
        title = LoobricTabs.record_name(record) if record.get("internal") \
            else (record.get("id") or "item")
        LoobricTabs.RecordInspector(record, parent=self, title=str(title)).exec_()

    def show_api_log(self):
        if self._api_panel is None:
            self._api_panel = LoobricTabs.ApiLogPanel(self.client, parent=self)
            self._api_panel.finished.connect(self._api_panel_closed)
        self._api_panel.show()
        self._api_panel.raise_()
        self._api_panel.refresh()

    def _api_panel_closed(self, *_):
        self._api_panel = None

    def refresh_api_log(self):
        if self._api_panel is not None:
            self._api_panel.refresh()


# Back-compat alias: the toolbar command historically opened LoobricSyncDialog.
LoobricSyncDialog = LoobricWindow
