# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Smooth — the tabbed desktop window.

A deliberately small shell of three tabs over a single shared API client (so all
HTTP traffic lands in one call log): **Sync** (the one CAM surface — plan/apply
of the local tool directory, with a needs-attention filter and per-set/-tool
management), **Machines** (the one binding surface — tool tables with the Inbox
folded in), and **Audit**. A header shows the server + connection state; raw
sectioned-record JSON inspection and a live API log are demoted behind a
**Debug** menu. The window is modeless so FreeCAD stays usable beside it.

All behavior lives in the headless-tested modules (client.py, sync.py,
mapping.py) and the pure view-model (viewmodel.py) consumed by SmoothTabs.py;
this file is the shell that wires them up.
"""
import json
from pathlib import Path
from typing import Dict

import FreeCAD as App
from PySide import QtGui, QtCore

from . import SmoothTabs
from .client import SmoothApi, SmoothError


class SmoothConfig:
    """Configuration in ~/.config/smooth/freecad.json (v1-compatible)."""

    @staticmethod
    def get_config_path() -> Path:
        config_dir = Path.home() / ".config" / "smooth"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "freecad.json"

    @staticmethod
    def load() -> Dict:
        default = {"api_url": "https://api.loobric.com", "api_key": ""}
        path = SmoothConfig.get_config_path()
        if path.exists():
            try:
                with open(path) as f:
                    default.update(json.load(f))
            except (OSError, ValueError) as e:
                App.Console.PrintWarning(f"Smooth: bad config file: {e}\n")
        url = default["api_url"].rstrip("/")
        if url.endswith("/api"):
            url = url[:-4]
        default["api_url"] = url
        return default

    @staticmethod
    def save(config: Dict) -> None:
        with open(SmoothConfig.get_config_path(), "w") as f:
            json.dump(config, f, indent=2)


def get_tools_dir() -> Path:
    """Locate FreeCAD's CAM tool assets directory."""
    try:
        from Path import Preferences
        asset_path = Path(Preferences.getAssetPath())
    except (ImportError, AttributeError):
        asset_path = Path(App.getUserAppDataDir()) / "Mod" / "Path"
    return asset_path / "Tools"


class SmoothWindow(QtGui.QDialog):
    """Tabbed Smooth window. Opens on the Sync tab; other tabs lazy-load when
    first shown. One client is shared by every tab. Modeless (see the command),
    so FreeCAD stays usable alongside it."""

    def __init__(self):
        super().__init__()
        self.config = SmoothConfig.load()
        self.client = SmoothApi(self.config["api_url"],
                                self.config.get("api_key", ""))
        self._api_panel = None
        self._build_ui()

    # -- UI ---------------------------------------------------------------

    def _build_ui(self):
        self.setWindowTitle("Smooth")
        self.resize(820, 620)
        layout = QtGui.QVBoxLayout(self)

        header = QtGui.QHBoxLayout()
        header.addWidget(QtGui.QLabel(f"Server: {self.config['api_url']}"))
        header.addStretch()
        self.conn_label = QtGui.QLabel("checking…")
        header.addWidget(self.conn_label)
        layout.addLayout(header)

        self.tabs = QtGui.QTabWidget()
        tools_dir = str(get_tools_dir())
        # The one CAM surface; binding lives on Machines; Audit is read-only.
        self.sync_tab = SmoothTabs.SyncTab(self, self.client, tools_dir)
        self._tab_list = [
            self.sync_tab,
            SmoothTabs.MachinesTab(self, self.client),
            SmoothTabs.AuditTab(self, self.client),
        ]
        for tab in self._tab_list:
            self.tabs.addTab(tab, tab.TITLE)
        self.tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self.tabs, stretch=1)

        self.status_label = QtGui.QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Raw-record inspection and the live API log are diagnostics, not primary
        # chrome — they live behind a Debug menu.
        bar = QtGui.QHBoxLayout()
        debug = QtGui.QPushButton("Debug ▾")
        debug_menu = QtGui.QMenu(debug)
        act_inspect = debug_menu.addAction("Inspect selected record (JSON)…")
        act_inspect.triggered.connect(self.inspect_selected)
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
            self.conn_label.setText("✓ connected")
        except SmoothError as e:
            self.conn_label.setText("✗ unreachable")
            self.status(f"Cannot reach server: {e}")
        self.refresh_api_log()

    def _tab_changed(self, index):
        widget = self.tabs.widget(index)
        if hasattr(widget, "ensure_loaded"):
            widget.ensure_loaded()

    # -- services the tabs call ------------------------------------------

    def status(self, message):
        self.status_label.setText(message)
        App.Console.PrintMessage("Smooth: %s\n" % message)
        QtGui.QApplication.processEvents()

    def inspect_selected(self):
        widget = self.tabs.currentWidget()
        record = widget.selected_record() if hasattr(widget, "selected_record") else None
        if not isinstance(record, dict):
            self.status("Select a row to inspect its record.")
            return
        title = SmoothTabs.record_name(record) if record.get("internal") \
            else (record.get("id") or "item")
        SmoothTabs.RecordInspector(record, parent=self, title=str(title)).exec_()

    def show_api_log(self):
        if self._api_panel is None:
            self._api_panel = SmoothTabs.ApiLogPanel(self.client, parent=self)
            self._api_panel.finished.connect(self._api_panel_closed)
        self._api_panel.show()
        self._api_panel.raise_()
        self._api_panel.refresh()

    def _api_panel_closed(self, *_):
        self._api_panel = None

    def refresh_api_log(self):
        if self._api_panel is not None:
            self._api_panel.refresh()


# Back-compat alias: the toolbar command historically opened SmoothSyncDialog.
SmoothSyncDialog = SmoothWindow
