# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Smooth sync dialog — plan/apply (smooth-freecad#7).

The dialog computes a sync plan and renders it for review: every library
and tool bit with its pending action, per-item checkboxes, and an explicit
keep-local/take-server choice on each conflict. Nothing touches disk or
the server until Apply.

All behavior lives in the headless-tested modules (mapping.py, client.py,
sync.py) — this file only renders and collects decisions.
"""
import json
from pathlib import Path
from typing import Dict

import FreeCAD as App
from PySide import QtGui, QtCore

from . import sync
from .client import SmoothClient, SmoothError


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


class SmoothSyncDialog:
    """Plan/apply sync dialog (smooth-freecad#7).

    Shows the computed sync plan as a tree - libraries with their member
    bits, each row carrying its pending action - with per-item checkboxes
    and per-conflict resolution choices. Nothing touches disk or server
    until Apply. All decisions execute through the headless-tested
    sync.plan_sync/apply_sync; this class only renders and collects.
    """

    ACTION_LABELS = {
        "unchanged": "in sync",
        "push": "will upload (changed here)",
        "pull": "will download (changed on server)",
        "new_local": "will upload (new)",
        "new_server": "will download (new on server)",
        "conflict": "CONFLICT - choose a side",
    }
    CONFLICT_CHOICES = ["skip for now", "keep local (upload)",
                        "take server (download)"]
    CONFLICT_DECISIONS = {1: "keep_local", 2: "take_server"}

    def __init__(self):
        self.config = SmoothConfig.load()
        self.plan = {"items": [], "errors": []}
        self._build_ui()

    def exec_(self):
        QtCore.QTimer.singleShot(50, self.refresh_plan)
        return self.form.exec_()

    # -- UI ---------------------------------------------------------------

    def _build_ui(self):
        self.form = QtGui.QDialog()
        self.form.setWindowTitle("Sync with Smooth")
        self.form.resize(720, 560)
        layout = QtGui.QVBoxLayout(self.form)

        server_row = QtGui.QHBoxLayout()
        self.status_label = QtGui.QLabel("Checking connection…")
        server_row.addWidget(QtGui.QLabel(f"Server: {self.config['api_url']}"))
        server_row.addStretch()
        server_row.addWidget(self.status_label)
        layout.addLayout(server_row)

        self.tree = QtGui.QTreeWidget()
        self.tree.setHeaderLabels(["Sync", "Item", "Status"])
        self.tree.setColumnWidth(0, 60)
        self.tree.setColumnWidth(1, 320)
        layout.addWidget(self.tree, stretch=3)

        self.log = QtGui.QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        layout.addWidget(self.log, stretch=1)

        buttons = QtGui.QHBoxLayout()
        self.refresh_button = QtGui.QPushButton("Refresh Plan")
        self.refresh_button.clicked.connect(self.refresh_plan)
        buttons.addWidget(self.refresh_button)
        self.apply_button = QtGui.QPushButton("Apply Selected")
        self.apply_button.clicked.connect(self._run_apply)
        self.apply_button.setEnabled(False)
        buttons.addWidget(self.apply_button)
        buttons.addStretch()
        close_button = QtGui.QPushButton("Close")
        close_button.clicked.connect(self.form.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def _append(self, message: str):
        self.log.append(message)
        QtGui.QApplication.processEvents()

    def _client(self) -> SmoothClient:
        return SmoothClient(self.config["api_url"], self.config.get("api_key", ""))

    # -- Plan rendering ------------------------------------------------------

    def refresh_plan(self):
        self.tree.clear()
        self._row_widgets = {}
        try:
            self._client().ping()
            self.status_label.setText("✓ connected")
        except SmoothError as e:
            self.status_label.setText("✗ unreachable")
            self._append(f"Cannot reach server: {e}")
            self.apply_button.setEnabled(False)
            return
        try:
            self.plan = sync.plan_sync(str(get_tools_dir()), self._client())
        except SmoothError as e:
            self._append(f"Planning failed: {e}")
            return
        for error in self.plan["errors"]:
            self._append(f"⚠ {error}")

        items = self.plan["items"]
        libraries = [i for i in items if i["kind"] == "library"]
        bits = [i for i in items if i["kind"] == "bit"]
        bits_by_library = {}
        for bit in bits:
            bits_by_library.setdefault(bit.get("library"), []).append(bit)

        pending = 0
        for library in sorted(libraries, key=lambda i: i["name"]):
            group = QtGui.QTreeWidgetItem(
                ["", f"📁 {library['name']}", ""])
            self.tree.addTopLevelItem(group)
            pending += self._add_row(group, library, indent_self=True)
            for bit in sorted(bits_by_library.get(library.get("basename"), []),
                              key=lambda i: i["name"]):
                pending += self._add_row(group, bit)
            group.setExpanded(True)
        loose = bits_by_library.get(None, [])
        if loose:
            group = QtGui.QTreeWidgetItem(["", "📄 Not in any library", ""])
            self.tree.addTopLevelItem(group)
            for bit in sorted(loose, key=lambda i: i["name"]):
                pending += self._add_row(group, bit)
            group.setExpanded(True)

        if pending:
            self._append(f"Plan ready: {pending} item(s) need attention. "
                         "Review, then Apply Selected.")
        else:
            self._append("Everything is in sync.")
        self.apply_button.setEnabled(pending > 0)

    def _add_row(self, parent, item, indent_self=False):
        """One tree row per plan item. Returns 1 if it needs attention."""
        label = ("(this library)" if indent_self else item["name"])
        row = QtGui.QTreeWidgetItem(["", label,
                                     self.ACTION_LABELS[item["action"]]])
        row.setToolTip(2, item["detail"])
        parent.addChild(row)
        if item["action"] == "unchanged":
            row.setDisabled(True)
            return 0
        if item["action"] == "conflict":
            combo = QtGui.QComboBox()
            combo.addItems(self.CONFLICT_CHOICES)
            self.tree.setItemWidget(row, 0, combo)
            self._row_widgets[item["key"]] = ("combo", combo)
        else:
            check = QtGui.QCheckBox()
            check.setChecked(True)
            self.tree.setItemWidget(row, 0, check)
            self._row_widgets[item["key"]] = ("check", check)
        return 1

    # -- Apply ------------------------------------------------------------------

    def _collect_decisions(self):
        decisions = {}
        for key, (kind, widget) in self._row_widgets.items():
            if kind == "check" and widget.isChecked():
                decisions[key] = "apply"
            elif kind == "combo" and widget.currentIndex() in self.CONFLICT_DECISIONS:
                decisions[key] = self.CONFLICT_DECISIONS[widget.currentIndex()]
        return decisions

    def _run_apply(self):
        decisions = self._collect_decisions()
        if not decisions:
            self._append("Nothing selected.")
            return
        self.apply_button.setEnabled(False)
        try:
            summary = sync.apply_sync(str(get_tools_dir()), self._client(),
                                      self.plan, decisions, log=self._append)
        except SmoothError as e:
            self._append(f"Apply failed: {e}")
            self.apply_button.setEnabled(True)
            return
        self._append(
            f"Done: {summary['pushed']} uploaded, {summary['pulled']} "
            f"downloaded, {summary['skipped']} skipped, "
            f"{len(summary['errors'])} error(s).")
        for error in summary["errors"]:
            self._append(f"  ⚠ {error}")
        if summary["pulled"]:
            self._append("Reload the tool library (or restart FreeCAD) to "
                         "see downloaded changes in the editor.")
        self.refresh_plan()
