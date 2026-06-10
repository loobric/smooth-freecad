# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Smooth sync dialog — v2.

Deliberately thin: all sync logic lives in the headless-tested modules
(mapping.py, client.py, sync.py); this dialog only gathers config, runs
export/import, and shows the summaries. Import never overwrites local
edits: pending changes are kept and conflicts are reported, not merged.

GUI code cannot be tested headless — keep anything with behavior OUT of
this file.
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
    """Minimal sync dialog: connection status, Export, summary log."""

    def __init__(self):
        self.config = SmoothConfig.load()
        self._build_ui()

    def exec_(self):
        QtCore.QTimer.singleShot(50, self._check_connection)
        return self.form.exec_()

    # -- UI ---------------------------------------------------------------

    def _build_ui(self):
        self.form = QtGui.QDialog()
        self.form.setWindowTitle("Sync with Smooth")
        self.form.resize(560, 420)
        layout = QtGui.QVBoxLayout(self.form)

        server_row = QtGui.QHBoxLayout()
        self.status_label = QtGui.QLabel("Checking connection…")
        server_row.addWidget(QtGui.QLabel(f"Server: {self.config['api_url']}"))
        server_row.addStretch()
        server_row.addWidget(self.status_label)
        layout.addLayout(server_row)

        self.log = QtGui.QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText(
            "Export uploads your tool bits and libraries to the Smooth "
            "server.\nFiles that were exported before update their existing "
            "records — nothing is ever duplicated."
        )
        layout.addWidget(self.log)

        buttons = QtGui.QHBoxLayout()
        self.export_button = QtGui.QPushButton("Export to Smooth")
        self.export_button.clicked.connect(self._run_export)
        buttons.addWidget(self.export_button)

        self.import_button = QtGui.QPushButton("Import from Smooth")
        self.import_button.clicked.connect(self._run_import)
        self.import_button.setToolTip(
            "Bring server-side changes into your tool files. Local edits "
            "are never overwritten: pending changes are kept, and "
            "both-sides-changed files are reported as conflicts."
        )
        buttons.addWidget(self.import_button)

        buttons.addStretch()
        close_button = QtGui.QPushButton("Close")
        close_button.clicked.connect(self.form.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def _append(self, message: str):
        self.log.append(message)
        QtGui.QApplication.processEvents()

    # -- Actions ------------------------------------------------------------

    def _client(self) -> SmoothClient:
        return SmoothClient(self.config["api_url"], self.config.get("api_key", ""))

    def _check_connection(self):
        try:
            self._client().ping()
            self.status_label.setText("✓ connected")
        except SmoothError as e:
            self.status_label.setText("✗ unreachable")
            self._append(f"Cannot reach server: {e}\n"
                         "Check Edit → Preferences → CAM → Smooth.")
            self.export_button.setEnabled(False)
            self.import_button.setEnabled(False)

    def _run_export(self):
        self.export_button.setEnabled(False)
        tools_dir = get_tools_dir()
        self._append(f"Exporting from {tools_dir} …")
        try:
            summary = sync.export_tools(str(tools_dir), self._client(),
                                        log=self._append)
        except SmoothError as e:
            self._append(f"\nExport failed: {e}")
            self.export_button.setEnabled(True)
            return
        self._append(
            f"\nDone: {summary['created']} created, "
            f"{summary['updated']} updated, {len(summary['errors'])} error(s)."
        )
        for error in summary["errors"]:
            self._append(f"  ⚠ {error}")
        self.export_button.setEnabled(True)

    def _run_import(self):
        self.import_button.setEnabled(False)
        tools_dir = get_tools_dir()
        self._append(f"Importing into {tools_dir} …")
        try:
            summary = sync.import_tools(str(tools_dir), self._client(),
                                        log=self._append)
        except SmoothError as e:
            self._append(f"\nImport failed: {e}")
            self.import_button.setEnabled(True)
            return
        self._append(
            f"\nDone: {summary['written']} written, "
            f"{summary['unchanged']} unchanged, "
            f"{summary['pending_export']} kept (local changes pending export), "
            f"{len(summary['conflicts'])} conflict(s), "
            f"{len(summary['errors'])} error(s)."
        )
        for conflict in summary["conflicts"]:
            self._append(f"  ⚠ CONFLICT: {conflict}")
        for error in summary["errors"]:
            self._append(f"  ⚠ {error}")
        if summary["written"]:
            self._append("Restart FreeCAD (or reload the tool library) to "
                         "see imported changes in the editor.")
        self.import_button.setEnabled(True)
