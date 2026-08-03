# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Loobric FreeCAD Addon - Preference Page.

Provides configuration UI for Loobric server connection in FreeCAD preferences.
This version loads the UI from a .ui file.
"""

from PySide import QtGui, QtCore, QtUiTools
import FreeCAD as App
import FreeCADGui as Gui
from pathlib import Path
import json
import os


class LoobricPreferencePage:
    """Preference page for Loobric addon settings."""
    
    def __init__(self):
        """Initialize the preference page by loading the .ui file."""
        # Load the .ui file
        ui_path = os.path.join(os.path.dirname(__file__), "LoobricPreferences.ui")
        loader = QtUiTools.QUiLoader()
        ui_file = QtCore.QFile(ui_path)
        ui_file.open(QtCore.QFile.ReadOnly)
        self.form = loader.load(ui_file)
        ui_file.close()
        
        # Get references to UI elements
        # Use QWidget for robust lookup regardless of Qt module mapping
        self.url_edit = self.form.findChild(QtGui.QWidget, "apiUrlEdit")
        self.key_edit = self.form.findChild(QtGui.QWidget, "apiKeyEdit")
        self.show_key_checkbox = self.form.findChild(QtGui.QCheckBox, "showKeyCheckbox")
        self.auto_sync_checkbox = self.form.findChild(QtGui.QCheckBox, "autoSyncCheckbox")
        self.asset_store_checkbox = self.form.findChild(QtGui.QCheckBox, "assetStoreCheckbox")
        self.test_button = self.form.findChild(QtGui.QPushButton, "testButton")
        self.status_label = self.form.findChild(QtGui.QLabel, "statusLabel")
        
        # Connect signals
        # Use both signals for maximum compatibility
        self.show_key_checkbox.toggled.connect(self.toggle_key_visibility)
        self.test_button.clicked.connect(self.test_connection)
        
        # Load current settings
        self.load_settings()
        # Apply initial echo mode based on current checkbox state
        self.toggle_key_visibility(self.show_key_checkbox.isChecked())
    
    def toggle_key_visibility(self, state):
        """Toggle API key visibility."""
        checked = self.show_key_checkbox.isChecked()
        mode = QtGui.QLineEdit.Normal if checked else QtGui.QLineEdit.Password
        self.key_edit.setEchoMode(mode)

    def _normalize_url(self, url: str) -> str:
        """Normalize base URL by removing trailing '/' and trailing '/api' if present."""
        if not url:
            return url
        url = url.strip().rstrip('/')
        if url.endswith('/api'):
            url = url[:-4]
        return url
    
    def get_config_path(self):
        """Get path to config file."""
        config_dir = Path.home() / ".config" / "loobric"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "freecad.json"
    
    def load_settings(self):
        """Load settings from config file."""
        try:
            config_path = self.get_config_path()
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    # Normalize URL so users can paste either base or base/api
                    url = self._normalize_url(config.get("api_url", ""))
                    self.url_edit.setText(url)
                    self.key_edit.setText(config.get("api_key", ""))
                    self.auto_sync_checkbox.setChecked(config.get("auto_sync", False))
                    if self.asset_store_checkbox is not None:
                        self.asset_store_checkbox.setChecked(
                            config.get("asset_store", False))
            # Ensure checkbox state applies immediately
            #self.toggle_key_visibility(self.show_key_checkbox.isChecked())
        except Exception as e:
            App.Console.PrintError(f"Failed to load Loobric settings: {e}\n")
    
    def saveSettings(self):
        """Save settings to config file (called by FreeCAD)."""
        try:
            # Merge over the existing file — other keys (e.g. state the
            # asset store writes) must survive a preferences save.
            config_path = self.get_config_path()
            config = {}
            if config_path.exists():
                try:
                    with open(config_path) as f:
                        config = json.load(f)
                except (OSError, ValueError):
                    config = {}
            config.update({
                # Save normalized base URL (without trailing '/api')
                "api_url": self._normalize_url(self.url_edit.text().strip()),
                "api_key": self.key_edit.text().strip(),
                "auto_sync": self.auto_sync_checkbox.isChecked(),
            })
            if self.asset_store_checkbox is not None:
                config["asset_store"] = self.asset_store_checkbox.isChecked()
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            App.Console.PrintMessage("Loobric settings saved\n")
            self.status_label.setText("✓ Settings saved successfully")
            
        except Exception as e:
            App.Console.PrintError(f"Failed to save Loobric settings: {e}\n")
            self.status_label.setText(f"✗ Failed to save settings: {str(e)}")
    
    def loadSettings(self):
        """Load settings (called by FreeCAD)."""
        self.load_settings()
    
    def test_connection(self):
        """Test connection to the Loobric server via the same stdlib client the
        sync uses (``LoobricApi.ping`` makes a cheap authenticated round-trip).
        Avoids a dependency on `requests`, which FreeCAD's bundled Python may not
        have."""
        url = self._normalize_url(self.url_edit.text().strip())
        api_key = self.key_edit.text().strip()

        if not url:
            self.status_label.setText("✗ Please enter a server URL")
            return

        try:
            from freecad.Loobric.client import LoobricApi, LoobricError
        except ImportError:
            from client import LoobricApi, LoobricError  # flat layout

        try:
            LoobricApi(url, api_key).ping()
            self.status_label.setText("✓ Connection successful!")
            App.Console.PrintMessage(
                f"Successfully connected to Loobric server at {url}\n")
        except LoobricError as e:
            self.status_label.setText("✗ Connection failed - cannot reach server")
            App.Console.PrintError(f"Connection test failed for {url}: {e}\n")
        except Exception as e:
            self.status_label.setText(f"✗ Error: {str(e)}")
            App.Console.PrintError(f"Connection test failed: {str(e)}\n")
