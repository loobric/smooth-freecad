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

from . import sync, mapping
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
        "push": "changed here",
        "pull": "changed on server",
        "new_local": "new here",
        "new_server": "new on server",
        "conflict": "changed on BOTH sides",
        "deleted_local": "deleted here",
        "deleted_server": "deleted on server",
    }
    DIRECTIONS = ["leave unsynced", "upload local \u2192 server",
                  "download server \u2192 local"]
    ROW_CHOICES = {
        "deleted_local": ["leave unsynced", "delete on server too",
                          "restore from server"],
        "deleted_server": ["leave unsynced", "upload again (restore)",
                           "delete local file too"],
    }
    DECISIONS = {1: "push", 2: "pull"}
    DEFAULT_DIRECTION = {"push": 1, "new_local": 1,
                         "pull": 2, "new_server": 2, "conflict": 0,
                         "deleted_local": 0, "deleted_server": 0}

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
        self.tree.setHeaderLabels(["Item", "Status", "Action", "Tool type"])
        self.tree.setColumnWidth(0, 250)
        self.tree.setColumnWidth(1, 150)
        self.tree.setColumnWidth(2, 150)
        self.tree.itemSelectionChanged.connect(self._show_diff)
        layout.addWidget(self.tree, stretch=3)

        self.diff_pane = QtGui.QTextEdit()
        self.diff_pane.setReadOnly(True)
        self.diff_pane.setMaximumHeight(130)
        self.diff_pane.setPlaceholderText(
            "Select a changed item to see exactly which fields differ, "
            "with each change attributed to the side that made it.")
        layout.addWidget(self.diff_pane, stretch=1)

        layout.addWidget(QtGui.QLabel("Log (also written to the Report view):"))
        self.log = QtGui.QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(150)
        layout.addWidget(self.log, stretch=2)

        buttons = QtGui.QHBoxLayout()
        self.refresh_button = QtGui.QPushButton("Refresh Plan")
        self.refresh_button.clicked.connect(self.refresh_plan)
        buttons.addWidget(self.refresh_button)
        defaults_button = QtGui.QPushButton("All: Suggested")
        defaults_button.setToolTip("Set every item to its suggested direction "
                                   "(conflicts stay 'leave unsynced')")
        defaults_button.clicked.connect(lambda: self._set_all(default=True))
        buttons.addWidget(defaults_button)
        skip_button = QtGui.QPushButton("All: Skip")
        skip_button.clicked.connect(lambda: self._set_all(default=False))
        buttons.addWidget(skip_button)
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
        # Mirror to FreeCAD's Report view so there's a persistent, copyable
        # log to debug from — the in-dialog pane scrolls and is lost on close.
        self.log.append(message)
        App.Console.PrintMessage("Smooth: %s\n" % message)
        QtGui.QApplication.processEvents()

    def _client(self) -> SmoothClient:
        return SmoothClient(self.config["api_url"], self.config.get("api_key", ""))

    # -- Plan rendering ------------------------------------------------------

    def refresh_plan(self):
        self.tree.clear()
        self._row_widgets = {}
        self._shape_widgets = {}
        try:
            self._client().ping()
            self.status_label.setText("✓ connected")
        except SmoothError as e:
            self.status_label.setText("✗ unreachable")
            self._append(f"Cannot reach server: {e}")
            self.apply_button.setEnabled(False)
            return
        try:
            self.plan = sync.plan_sync(str(get_tools_dir()), self._client(),
                                       log=self._append)
        except SmoothError as e:
            self._append(f"Planning failed: {e}")
            return
        for error in self.plan["errors"]:
            self._append(f"⚠ {error}")

        items = self.plan["items"]
        libraries = [i for i in items if i["kind"] == "library"]
        bits = [i for i in items if i["kind"] == "bit"]
        # Group bits by their owning library (local OR server) via the plan's
        # stable `group` handle. Each bit is rendered AT MOST ONCE (the `shown`
        # guard), so a tool never appears under both a library and "not in any
        # library" — and never gets a second combo that shadows the first.
        bits_by_group = {}
        for bit in bits:
            bits_by_group.setdefault(bit.get("group"), []).append(bit)

        pending = 0
        shown = set()
        for library in sorted(libraries, key=lambda i: i["name"]):
            node = QtGui.QTreeWidgetItem(["", f"📁 {library['name']}", "", ""])
            self.tree.addTopLevelItem(node)
            pending += self._add_row(node, library, indent_self=True)
            for bit in sorted(bits_by_group.get(library["group"], []),
                              key=lambda i: i["name"]):
                pending += self._add_row(node, bit)
                shown.add(bit["key"])
            node.setExpanded(True)
        loose = [b for b in sorted(bits, key=lambda i: i["name"])
                 if b["key"] not in shown]
        if loose:
            node = QtGui.QTreeWidgetItem(["", "📄 Not in any library", "", ""])
            self.tree.addTopLevelItem(node)
            for bit in loose:
                pending += self._add_row(node, bit)
            node.setExpanded(True)

        if pending:
            self._append(f"Plan ready: {pending} item(s) need attention. "
                         "Review, then Apply Selected.")
        else:
            self._append("Everything is in sync.")
        self.apply_button.setEnabled(pending > 0)

    def _add_row(self, parent, item, indent_self=False):
        """One tree row per plan item. Returns 1 if it needs attention."""
        label = ("(this library)" if indent_self else item["name"])
        row = QtGui.QTreeWidgetItem([label,
                                     self.ACTION_LABELS[item["action"]], ""])
        row.setToolTip(1, item["detail"])
        row.setData(0, QtCore.Qt.UserRole, item["key"])
        parent.addChild(row)
        if item["action"] == "unchanged":
            row.setDisabled(True)
            return 0
        combo = QtGui.QComboBox()
        combo.addItems(self.ROW_CHOICES.get(item["action"], self.DIRECTIONS))
        combo.setCurrentIndex(self.DEFAULT_DIRECTION[item["action"]])
        if item["action"] not in self.ROW_CHOICES:
            if item["path"] is None:
                combo.model().item(1).setEnabled(False)   # nothing local
            if item["record"] is None:
                combo.model().item(2).setEnabled(False)   # nothing on server
        self.tree.setItemWidget(row, 2, combo)
        self._row_widgets[item["key"]] = combo
        # Server-originated tools have no FreeCAD shape; let the user pick the
        # tool type BEFORE the .fctb is synthesized (FreeCAD locks it after).
        if sync.needs_shape_choice(item):
            shape_combo = QtGui.QComboBox()
            shape_combo.addItems(mapping.FREECAD_SHAPES)
            current = mapping.record_shape(item["record"]) or mapping.DEFAULT_SHAPE
            if current in mapping.FREECAD_SHAPES:
                shape_combo.setCurrentIndex(mapping.FREECAD_SHAPES.index(current))
            shape_combo.setToolTip(
                "Tool type to create on download. FreeCAD can't change a bit's "
                "type after creation. If the server shows the wrong type (e.g. "
                "endmill), set the correct one here to rebuild it.")
            self.tree.setItemWidget(row, 3, shape_combo)
            self._shape_widgets[item["key"]] = shape_combo
        return 1

    def _set_all(self, default):
        items_by_key = {i["key"]: i for i in self.plan["items"]}
        for key, combo in self._row_widgets.items():
            if default:
                combo.setCurrentIndex(
                    self.DEFAULT_DIRECTION[items_by_key[key]["action"]])
            else:
                combo.setCurrentIndex(0)

    def _show_diff(self):
        selected = self.tree.selectedItems()
        if not selected:
            return
        key = selected[0].data(0, QtCore.Qt.UserRole)
        item = next((i for i in self.plan["items"] if i["key"] == key), None)
        if not item:
            self.diff_pane.setPlainText("")
            return
        lines = [item["detail"]]
        for d in item.get("diff", []):
            side = {"local": "changed here",
                    "server": "changed on server",
                    "both": "changed on BOTH sides"}[d["changed_by"]]
            lines.append("  %s: %r (local)  vs  %r (server)   [%s]"
                         % (d["field"], d["local"], d["server"], side))
        if item["action"] == "new_local":
            lines.append("  (entire file is new to the server)")
        if item["action"] == "new_server":
            lines.append("  (entire record is new - downloading creates the file)")
        self.diff_pane.setPlainText("\n".join(lines))

    # -- Apply ------------------------------------------------------------------

    def _collect_decisions(self):
        decisions = {}
        for key, combo in self._row_widgets.items():
            if combo.currentIndex() in self.DECISIONS:
                decisions[key] = self.DECISIONS[combo.currentIndex()]
        return decisions

    def _collect_shapes(self):
        return {key: combo.currentText()
                for key, combo in self._shape_widgets.items()}

    def _run_apply(self):
        decisions = self._collect_decisions()
        if not decisions:
            self._append("Nothing selected.")
            return
        self.apply_button.setEnabled(False)
        try:
            summary = sync.apply_sync(str(get_tools_dir()), self._client(),
                                      self.plan, decisions,
                                      shapes=self._collect_shapes(),
                                      log=self._append)
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
