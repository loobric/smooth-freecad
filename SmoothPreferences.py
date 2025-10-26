# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Smooth FreeCAD Addon - Preference Page.

Provides configuration UI for Smooth server connection in FreeCAD preferences.
This version loads the UI from a .ui file.
"""

from PySide import QtGui, QtCore, QtUiTools
import FreeCAD as App
import FreeCADGui as Gui
from pathlib import Path
import json
import os


class SmoothPreferencePage:
    """Preference page for Smooth addon settings."""
    
    def __init__(self):
        """Initialize the preference page by loading the .ui file."""
        # Load the .ui file
        ui_path = os.path.join(os.path.dirname(__file__), "SmoothPreferences.ui")
        loader = QtUiTools.QUiLoader()
        ui_file = QtCore.QFile(ui_path)
        ui_file.open(QtCore.QFile.ReadOnly)
        self.form = loader.load(ui_file)
        ui_file.close()
        
        # Get references to UI elements
        self.url_edit = self.form.findChild(QtGui.QLineEdit, "apiUrlEdit")
        self.key_edit = self.form.findChild(QtGui.QLineEdit, "apiKeyEdit")
        self.show_key_checkbox = self.form.findChild(QtGui.QCheckBox, "showKeyCheckbox")
        self.auto_sync_checkbox = self.form.findChild(QtGui.QCheckBox, "autoSyncCheckbox")
        self.test_button = self.form.findChild(QtGui.QPushButton, "testButton")
        self.status_label = self.form.findChild(QtGui.QLabel, "statusLabel")
        
        # Connect signals
        self.show_key_checkbox.stateChanged.connect(self.toggle_key_visibility)
        self.test_button.clicked.connect(self.test_connection)
        
        # Load current settings
        self.load_settings()
    
    def toggle_key_visibility(self, state):
        """Toggle API key visibility."""
        if state == QtCore.Qt.Checked:
            self.key_edit.setEchoMode(QtGui.QLineEdit.Normal)
        else:
            self.key_edit.setEchoMode(QtGui.QLineEdit.Password)
    
    def get_config_path(self):
        """Get path to config file."""
        config_dir = Path.home() / ".config" / "smooth"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "freecad.json"
    
    def load_settings(self):
        """Load settings from config file."""
        try:
            config_path = self.get_config_path()
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    self.url_edit.setText(config.get("api_url", ""))
                    self.key_edit.setText(config.get("api_key", ""))
                    self.auto_sync_checkbox.setChecked(config.get("auto_sync", False))
        except Exception as e:
            App.Console.PrintError(f"Failed to load Smooth settings: {e}\n")
    
    def saveSettings(self):
        """Save settings to config file (called by FreeCAD)."""
        try:
            config = {
                "api_url": self.url_edit.text().strip(),
                "api_key": self.key_edit.text().strip(),
                "auto_sync": self.auto_sync_checkbox.isChecked()
            }
            
            config_path = self.get_config_path()
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            App.Console.PrintMessage("Smooth settings saved\n")
            self.status_label.setText("✓ Settings saved successfully")
            
        except Exception as e:
            App.Console.PrintError(f"Failed to save Smooth settings: {e}\n")
            self.status_label.setText(f"✗ Failed to save settings: {str(e)}")
    
    def loadSettings(self):
        """Load settings (called by FreeCAD)."""
        self.load_settings()
    
    def test_connection(self):
        """Test connection to Smooth server."""
        url = self.url_edit.text().strip()
        api_key = self.key_edit.text().strip()
        
        if not url:
            self.status_label.setText("✗ Please enter a server URL")
            return
        
        try:
            import requests
            
            # Test health endpoint
            test_url = f"{url}/api/health" if "/api" not in url else f"{url}/health"
            
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            
            response = requests.get(test_url, headers=headers, timeout=5)
            
            if response.status_code == 200:
                self.status_label.setText("✓ Connection successful!")
                App.Console.PrintMessage(f"Successfully connected to Smooth server at {url}\n")
            else:
                self.status_label.setText(f"✗ Connection failed: HTTP {response.status_code}")
                App.Console.PrintError(f"Connection failed: HTTP {response.status_code}\n")
                
        except ImportError:
            self.status_label.setText("✗ Error: requests library not installed")
            App.Console.PrintError("requests library not installed\n")
        except requests.exceptions.Timeout:
            self.status_label.setText("✗ Connection timeout - server not responding")
            App.Console.PrintError(f"Connection timeout for {url}\n")
        except requests.exceptions.ConnectionError:
            self.status_label.setText("✗ Connection error - cannot reach server")
            App.Console.PrintError(f"Cannot connect to {url}\n")
        except Exception as e:
            self.status_label.setText(f"✗ Error: {str(e)}")
            App.Console.PrintError(f"Connection test failed: {str(e)}\n")
