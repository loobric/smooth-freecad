# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Smooth FreeCAD Addon - Dialog implementations.

UI dialogs for synchronizing FreeCAD tools with Smooth.
"""
import os
import json
from pathlib import Path
from typing import Dict, List, Tuple
from PySide import QtWidgets, QtCore, QtGui
import FreeCAD as App
import FreeCADGui

try:
    from fctb_parser import parse_fctb, fctb_to_smooth, smooth_to_fctb
    from fctl_parser import parse_fctl, load_library_with_tools
    from shape_storage import prepare_shape_upload, resolve_shape_file_path, download_and_save_shape
except ModuleNotFoundError:
    from clients.freecad.fctb_parser import parse_fctb, fctb_to_smooth, smooth_to_fctb
    from clients.freecad.fctl_parser import parse_fctl, load_library_with_tools
    from clients.freecad.shape_storage import prepare_shape_upload, resolve_shape_file_path, download_and_save_shape


class SmoothConfig:
    """Configuration manager for Smooth connection settings."""
    
    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize base URL: remove trailing slash and a trailing '/api' if present."""
        if not url:
            return url
        url = url.strip().rstrip('/')
        # If the URL ends with '/api', strip it so callers can append '/api/v1/...'
        if url.endswith('/api'):
            url = url[:-4]
        return url

    @staticmethod
    def get_config_path() -> Path:
        """Get path to config file."""
        config_dir = Path.home() / ".config" / "smooth"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "freecad.json"
    
    @staticmethod
    def load() -> Dict:
        """Load configuration from file."""
        config_path = SmoothConfig.get_config_path()
        default_config = {
            "api_url": "https://api.loobric.com",
            "api_key": "",
            "auto_sync": False,
            "machine_id": "freecad_default"
        }
        if config_path.exists():
            with open(config_path, 'r') as f:
                loaded = {**default_config, **json.load(f)}
                # Normalize URL if user stored with trailing '/api' or '/'
                loaded["api_url"] = SmoothConfig._normalize_url(loaded.get("api_url", ""))
                return loaded
        # Normalize default
        default_config["api_url"] = SmoothConfig._normalize_url(default_config["api_url"])
        return default_config
    
    @staticmethod
    def save(config: Dict) -> None:
        """Save configuration to file."""
        with open(SmoothConfig.get_config_path(), 'w') as f:
            json.dump(config, f, indent=2)



class SmoothSyncDialog:
    """Dialog for bidirectional sync with Smooth."""
    
    def __init__(self):
        self.config = SmoothConfig.load()
        self.setup_ui()
        self.load_data()
    
    def exec_(self):
        """Show the dialog and wait for user action."""
        return self.form.exec_()
    
    def get_freecad_paths(self) -> Tuple[Path, Path, Path]:
        """Get FreeCAD tool directories with fallback."""
        try:
            from Path import Preferences
            asset_path = Path(Preferences.getAssetPath())
        except (ImportError, AttributeError):
            asset_path = Path(App.getUserAppDataDir()) / "Mod" / "Path"
        tool_bits_dir = asset_path / "Tools" / "Bit"
        tool_library_dir = asset_path / "Tools" / "Library"
        shapes_dir = asset_path / "Tools" / "Shape"
        tool_bits_dir.mkdir(parents=True, exist_ok=True)
        tool_library_dir.mkdir(parents=True, exist_ok=True)
        shapes_dir.mkdir(parents=True, exist_ok=True)
        return tool_bits_dir, tool_library_dir, shapes_dir
    
    def setup_ui(self):
        """Setup the user interface by loading from .ui file."""
        ui_file_path = Path(__file__).parent / "SmoothDialog.ui"
        self.form = FreeCADGui.PySideUic.loadUi(str(ui_file_path))
        
        # Connect signals - access widgets directly from form
        self.form.selectAllBits.stateChanged.connect(self.toggle_all_bits)
        self.form.selectAllLibraries.stateChanged.connect(self.toggle_all_libraries)
        self.form.librariesList.itemClicked.connect(self.preview_library)
        self.form.selectAllItems.stateChanged.connect(self.toggle_all_items)
        self.form.selectAllSets.stateChanged.connect(self.toggle_all_sets)
        self.form.toolSetsList.itemClicked.connect(self.preview_toolset)
        self.form.syncButton.clicked.connect(self.start_sync)
        self.form.closeButton.clicked.connect(self.form.close)
        
        # Connect refresh buttons
        self.form.refreshBitsButton.clicked.connect(self.load_tool_bits)
        self.form.refreshLibrariesButton.clicked.connect(self.load_libraries)
        self.form.refreshItemsButton.clicked.connect(self.load_tool_items)
        self.form.refreshSetsButton.clicked.connect(self.load_tool_sets)
        self.form.viewHistoryButton.clicked.connect(self.show_toolset_history)
    
    def show_toolset_history(self):
        """Show version history for selected ToolSet."""
        import requests
        
        # Get selected ToolSet
        selected = self.form.toolSetsList.selectedItems()
        if not selected:
            QtGui.QMessageBox.warning(
                self.form,
                "No Selection",
                "Please select a ToolSet to view its history."
            )
            return
        
        if len(selected) > 1:
            QtGui.QMessageBox.warning(
                self.form,
                "Multiple Selection",
                "Please select only one ToolSet to view history."
            )
            return
        
        toolset = selected[0].data(QtCore.Qt.UserRole)
        toolset_id = toolset.get("id")
        toolset_name = toolset.get("name", "Unknown")
        
        # Fetch version history
        url = self.config.get("api_url", "http://localhost:8000").rstrip("/")
        headers = self.get_api_headers()
        
        try:
            response = requests.get(
                f"{url}/api/v1/tool-sets/{toolset_id}/history",
                headers=headers,
                timeout=10
            )
            response.raise_for_status()
            history_data = response.json()
            versions = history_data.get("versions", [])
            
            if not versions:
                QtGui.QMessageBox.information(
                    self.form,
                    "No History",
                    f"No version history found for '{toolset_name}'.\n\n"
                    f"History snapshots are created when a ToolSet is updated."
                )
                return
            
            # Create version history dialog
            from PySide import QtWidgets
            
            dialog = QtWidgets.QDialog(self.form)
            dialog.setWindowTitle(f"Version History: {toolset_name}")
            dialog.setMinimumWidth(600)
            layout = QtWidgets.QVBoxLayout()
            
            # Header info
            current_version = toolset.get('version', 1)
            info_label = QtWidgets.QLabel(
                f"<b>ToolSet:</b> {toolset_name}<br>"
                f"<b>Current Version:</b> v{current_version}<br>"
                f"<b>Total Versions:</b> {len(versions) + 1}"
            )
            layout.addWidget(info_label)
            
            # Version list
            version_list = QtWidgets.QListWidget()
            version_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            
            # Add current version
            current_text = f"v{current_version} - (current) - {len(toolset.get('members', []))} tools"
            current_item = QtWidgets.QListWidgetItem(current_text)
            current_item.setData(QtCore.Qt.UserRole, None)  # No restore for current
            version_list.addItem(current_item)
            
            # Add historical versions (newest to oldest)
            for version_info in sorted(versions, key=lambda v: v.get('version', 0), reverse=True):
                version = version_info.get("version")
                changed_at = version_info.get("changed_at", "unknown")[:19]
                summary = version_info.get("change_summary", "")
                
                # Note: History API doesn't include snapshot data, just metadata
                text = f"v{version} - {changed_at}"
                if summary:
                    text += f" - {summary}"
                
                item = QtWidgets.QListWidgetItem(text)
                item.setData(QtCore.Qt.UserRole, version_info)
                version_list.addItem(item)
            
            layout.addWidget(version_list)
            
            # Buttons
            button_layout = QtWidgets.QHBoxLayout()
            restore_btn = QtWidgets.QPushButton("Restore Selected")
            close_btn = QtWidgets.QPushButton("Close")
            button_layout.addWidget(restore_btn)
            button_layout.addWidget(close_btn)
            layout.addLayout(button_layout)
            
            dialog.setLayout(layout)
            
            def restore_selected():
                selected = version_list.selectedItems()
                if not selected:
                    QtGui.QMessageBox.warning(dialog, "No Selection", "Please select a version to restore.")
                    return
                
                version_info = selected[0].data(QtCore.Qt.UserRole)
                if version_info is None:
                    QtGui.QMessageBox.information(dialog, "Current Version", "This is already the current version.")
                    return
                
                target_version = version_info.get("version")
                
                # Confirm restore
                reply = QtGui.QMessageBox.question(
                    dialog,
                    "Confirm Restore",
                    f"Restore '{toolset_name}' to version {target_version}?\n\n"
                    f"This will:\n"
                    f"1. Restore v{target_version} on server (creates new version)\n"
                    f"2. Update your local FreeCAD library file\n\n"
                    f"Your local .fctl file will be overwritten.",
                    QtGui.QMessageBox.Yes | QtGui.QMessageBox.No
                )
                
                if reply == QtGui.QMessageBox.Yes:
                    try:
                        # Call restore API - it returns the restored ToolSet
                        App.Console.PrintMessage(f"Calling restore API for v{target_version}...\n")
                        restore_response = requests.post(
                            f"{url}/api/v1/tool-sets/{toolset_id}/restore/{target_version}",
                            headers=headers,
                            timeout=10
                        )
                        restore_response.raise_for_status()
                        restore_result = restore_response.json()
                        
                        # Extract the tool_set from the response
                        restored_toolset = restore_result.get('tool_set', restore_result)
                        App.Console.PrintMessage(f"Restored ToolSet v{restored_toolset.get('version')} with {len(restored_toolset.get('members', []))} members\n")
                        
                        # Update local file
                        self._update_local_library(toolset_name, restored_toolset)
                        
                        QtGui.QMessageBox.information(
                            dialog,
                            "Restore Complete",
                            f"Successfully restored '{toolset_name}' to v{target_version}.\n\n"
                            f"Your local FreeCAD library has been updated with {len(restored_toolset.get('members', []))} tools."
                        )
                        dialog.accept()
                        
                        # Refresh the toolsets list
                        self.load_tool_sets()
                    except Exception as e:
                        App.Console.PrintError(f"Restore error: {e}\n")
                        import traceback
                        traceback.print_exc()
                        QtGui.QMessageBox.critical(dialog, "Error", f"Failed to restore version: {str(e)}")
            
            restore_btn.clicked.connect(restore_selected)
            close_btn.clicked.connect(dialog.reject)
            
            dialog.exec_()
            
        except Exception as e:
            QtGui.QMessageBox.critical(
                self.form,
                "Error",
                f"Failed to fetch version history: {str(e)}"
            )
    
    def _update_local_library(self, toolset_name: str, restored_toolset: Dict):
        """Update local .fctl file with restored ToolSet data."""
        _, tool_library_dir, _ = self.get_freecad_paths()
        
        # Reconstruct .fctl from ToolSet
        members = restored_toolset.get('members', [])
        library = {
            "version": 1,
            "label": toolset_name,
            "tools": [
                {"nr": m.get('tool_number'), "path": m.get('tool_path')}
                for m in members
                if m.get('tool_number') is not None and m.get('tool_path')
            ]
        }
        
        # Save to file
        filepath = tool_library_dir / f"{toolset_name}.fctl"
        with open(filepath, 'w') as f:
            json.dump(library, f, indent=2)
        
        App.Console.PrintMessage(f"Updated local library: {filepath}\n")
    
    def load_data(self):
        """Load initial data for both tabs."""
        self.load_tool_bits()
        self.load_libraries()
        self.load_tool_items()
        self.load_tool_sets()
    
    def toggle_all_bits(self, state):
        """Toggle selection of all tool bits."""
        for i in range(self.form.toolBitsList.count()):
            item = self.form.toolBitsList.item(i)
            item.setSelected(state == QtCore.Qt.Checked)
    
    def toggle_all_libraries(self, state):
        """Toggle selection of all libraries."""
        for i in range(self.form.librariesList.count()):
            item = self.form.librariesList.item(i)
            item.setSelected(state == QtCore.Qt.Checked)
    
    def toggle_all_items(self, state):
        """Toggle selection of all tool items."""
        for i in range(self.form.toolItemsList.count()):
            item = self.form.toolItemsList.item(i)
            item.setSelected(state == QtCore.Qt.Checked)
    
    def toggle_all_sets(self, state):
        """Toggle selection of all tool sets."""
        for i in range(self.form.toolSetsList.count()):
            item = self.form.toolSetsList.item(i)
            item.setSelected(state == QtCore.Qt.Checked)
    
    def load_tool_bits(self):
        """Load FreeCAD tool bits."""
        self.form.toolBitsList.clear()
        tool_bits_dir, _, _ = self.get_freecad_paths()
        if not tool_bits_dir.exists():
            self.form.statusText.append("No tool bits directory found")
            return
        
        for fctb_file in tool_bits_dir.glob("*.fctb"):
            try:
                tool = parse_fctb(fctb_file)
                item = QtWidgets.QListWidgetItem(f"{tool.get('name', fctb_file.stem)} ({fctb_file.name})")
                item.setData(QtCore.Qt.UserRole, fctb_file)
                self.form.toolBitsList.addItem(item)
            except Exception as e:
                self.log_error(f"Failed to parse {fctb_file}: {e}")
        
        if self.form.selectAllBits.isChecked():
            self.form.toolBitsList.selectAll()
        self.form.statusText.append(f"Loaded {self.form.toolBitsList.count()} tool bits")
    
    def load_libraries(self):
        """Load FreeCAD tool libraries."""
        if not self.form.librariesList:
            return
        self.form.librariesList.clear()
        _, tool_library_dir, _ = self.get_freecad_paths()
        if not tool_library_dir.exists():
            self.form.statusText.append("No tool library directory found")
            return
        
        for fctl_file in tool_library_dir.glob("*.fctl"):
            try:
                library = parse_fctl(fctl_file)
                library_name = library.get("label", fctl_file.stem)
                item = QtWidgets.QListWidgetItem(f"{library_name} ({len(library.get('tools', []))} tools)")
                item.setData(QtCore.Qt.UserRole, {"file": fctl_file, "library": library})
                self.form.librariesList.addItem(item)
            except Exception as e:
                self.log_error(f"Failed to parse {fctl_file}: {e}")
        
        if self.form.selectAllLibraries.isChecked():
            self.form.librariesList.selectAll()
        self.form.statusText.append(f"Loaded {self.form.librariesList.count()} libraries")
    
    def load_tool_items(self):
        """Load tool items from Smooth."""
        if not self.form.toolItemsList:
            return
        self.form.toolItemsList.clear()
        try:
            import requests
            headers = self.get_api_headers()
            response = requests.get(f"{self.config['api_url'].rstrip('/')}/api/v1/tool-items", headers=headers, timeout=10)
            response.raise_for_status()
            items = response.json().get("items", [])
            
            for item in items:
                desc = item.get('description', 'Unknown')
                item_type = item.get('type', '')
                diam = item.get('geometry', {}).get('diameter')
                unit = item.get('geometry', {}).get('diameter_unit', '')
                text = f"{desc} ({item_type}, Ø{diam}{unit})" if diam else f"{desc} ({item_type})"
                list_item = QtWidgets.QListWidgetItem(text)
                list_item.setData(QtCore.Qt.UserRole, item)
                self.form.toolItemsList.addItem(list_item)
            
            if self.form.selectAllItems.isChecked():
                self.form.toolItemsList.selectAll()
            self.form.statusText.append(f"Loaded {len(items)} tool items")
        except Exception as e:
            self.log_error(f"Failed to load tool items: {e}")
    
    def load_tool_sets(self):
        """Load tool sets from Smooth."""
        if not self.form.toolSetsList:
            return
        self.form.toolSetsList.clear()
        try:
            import requests
            headers = self.get_api_headers()
            response = requests.get(
                f"{self.config['api_url'].rstrip('/')}/api/v1/tool-sets",
                headers=headers,
                params={"type": "template"},
                timeout=10
            )
            response.raise_for_status()
            sets = response.json().get("items", [])
            
            for toolset in sets:
                name = toolset.get('name', 'Unknown')
                members = toolset.get('members', [])
                source = toolset.get('activation', {}).get('source', '')
                text = f"{name} ({len(members)} tools) [FreeCAD]" if source == 'freecad' else f"{name} ({len(members)} tools)"
                item = QtWidgets.QListWidgetItem(text)
                item.setData(QtCore.Qt.UserRole, toolset)
                self.form.toolSetsList.addItem(item)
            
            if self.form.selectAllSets.isChecked():
                self.form.toolSetsList.selectAll()
            self.form.statusText.append(f"Loaded {len(sets)} tool sets")
        except Exception as e:
            self.log_error(f"Failed to load tool sets: {e}")
    
    def preview_library(self, item):
        """Preview FreeCAD library contents."""
        library = item.data(QtCore.Qt.UserRole)["library"]
        text = f"Library: {library.get('label', 'Unknown')}\nTools: {len(library.get('tools', []))}\n\n"
        for tool in library.get("tools", []):
            text += f"  T{tool.get('nr')}: {tool.get('path')}\n"
        self.form.libraryPreview.setText(text)
    
    def preview_toolset(self, item):
        """Preview Smooth tool set contents."""
        toolset = item.data(QtCore.Qt.UserRole)
        text = f"Name: {toolset.get('name')}\nType: {toolset.get('type')}\nStatus: {toolset.get('status')}\n\nMembers ({len(toolset.get('members', []))}):\n"
        for member in toolset.get('members', [])[:10]:
            text += f"  T{member.get('tool_number', '?')}: {member.get('description', member.get('tool_path', 'Unknown'))}\n"
        if len(toolset.get('members', [])) > 10:
            text += f"  ... and {len(toolset['members']) - 10} more\n"
        self.form.toolsetPreview.setText(text)
    
    def get_api_headers(self) -> Dict:
        """Get API headers with optional authorization."""
        headers = {"Content-Type": "application/json"}
        if self.config.get("api_key"):
            headers["Authorization"] = f"Bearer {self.config['api_key'].strip()}"
        return headers
    
    def log_error(self, message: str):
        """Log error to status text and FreeCAD console."""
        self.form.statusText.append(f"✗ {message}")
        App.Console.PrintError(f"{message}\n")
    
    def check_duplicates(self, tool: Dict, existing_by_id: Dict, existing_by_sig: Dict) -> Tuple[bool, str]:
        """Check if a tool is a duplicate."""
        freecad_id = tool.get("id")
        desc = tool.get("description", "")
        diam = tool.get("geometry", {}).get("diameter")
        
        if freecad_id and freecad_id in existing_by_id:
            return True, "FreeCAD ID"
        if desc and diam is not None and f"{desc}|{diam}" in existing_by_sig:
            return True, "description+diameter"
        return False, ""
    
    def export_tool_bits(self, url: str, headers: Dict, tool_bits_dir: Path) -> Tuple[int, int]:
        """Export selected tool bits to Smooth."""
        import requests
        selected_bits = self.form.toolBitsList.selectedItems()
        if not selected_bits:
            return 0, 0
        
        tools_exported, errors, skipped = 0, 0, 0
        smooth_tools, tool_names = [], []
        existing_by_id, existing_by_sig = {}, {}
        
        # Fetch existing tools
        try:
            response = requests.get(f"{url}/api/v1/tool-items", headers=headers, timeout=10)
            response.raise_for_status()
            for item in response.json().get("items", []):
                if fc_id := item.get("freecad_metadata", {}).get("id"):
                    existing_by_id[fc_id] = item
                desc = item.get("description", "")
                diam = item.get("geometry", {}).get("diameter")
                if desc and diam is not None:
                    existing_by_sig[f"{desc}|{diam}"] = item
            self.form.statusText.append(f"Found {len(existing_by_id)} existing tools")
        except Exception as e:
            self.log_error(f"Could not fetch existing tools: {e}")
        
        for idx, item in enumerate(selected_bits):
            fctb_file = item.data(QtCore.Qt.UserRole)
            try:
                tool = parse_fctb(fctb_file)
                smooth_tool = fctb_to_smooth(tool)
                is_duplicate, method = self.check_duplicates(smooth_tool, existing_by_id, existing_by_sig)
                
                if is_duplicate:
                    skipped += 1
                    self.form.statusText.append(f"⊙ Skipping duplicate: {tool.get('name')} ({method})")
                    continue
                
                if shape_data := smooth_tool.get('shape_data', {}):
                    if shape_filename := shape_data.get('reference', {}).get('value'):
                        shape_path = resolve_shape_file_path(shape_filename, tool_bits_dir.parent, [tool_bits_dir, tool_bits_dir.parent / "Shape"])
                        if shape_path and shape_path.exists():
                            try:
                                shape_upload = prepare_shape_upload(shape_path)
                                shape_data['reference'].update({
                                    'type': 'inline',
                                    'content': shape_upload['content'],
                                    'hash': shape_upload['hash'],
                                    'size_bytes': shape_upload['size_bytes']
                                })
                            except Exception as e:
                                self.log_error(f"Could not include shape {shape_filename}: {e}")
                
                smooth_tools.append(smooth_tool)
                tool_names.append(tool.get('name', fctb_file.stem))
            except Exception as e:
                errors += 1
                self.log_error(f"Failed to parse {fctb_file.stem}: {e}")
            
            self.form.progressBar.setValue(int((idx + 1) / len(selected_bits) * 25))
        
        if smooth_tools:
            try:
                response = requests.post(f"{url}/api/v1/tool-items", headers=headers, json={"items": smooth_tools}, timeout=30)
                response.raise_for_status()
                tools_exported = len(smooth_tools)
                for name in tool_names:
                    self.form.statusText.append(f"✓ {name}")
                if skipped:
                    self.form.statusText.append(f"⊙ Skipped {skipped} duplicates")
            except Exception as e:
                errors += len(smooth_tools)
                self.log_error(f"Bulk export failed: {e}")
        
        return tools_exported, errors
    
    def export_libraries(self, url: str, headers: Dict, tool_bits_dir: Path, tool_library_dir: Path) -> Tuple[int, int]:
        """Export selected libraries as ToolSets and ToolPresets."""
        import requests
        selected_libraries = self.form.librariesList.selectedItems()
        if not selected_libraries:
            return 0, 0
        
        libraries_exported, errors = 0, 0
        tool_sets, toolset_names, tool_presets = [], [], []
        toolset_map = {}
        machine_id = self.config.get("machine_id", "freecad_default")
        
        # Fetch existing toolsets
        existing_toolsets = {}
        try:
            response = requests.get(f"{url}/api/v1/tool-sets", headers=headers, params={"type": "template"}, timeout=10)
            response.raise_for_status()
            for item in response.json().get("items", []):
                if item.get("activation", {}).get("source") == "freecad":
                    existing_toolsets[item.get("name")] = item
        except Exception as e:
            self.log_error(f"Could not fetch tool sets: {e}")
        
        # Create ToolSets
        for item in selected_libraries:
            data = item.data(QtCore.Qt.UserRole)
            library = data["library"]
            fctl_file = data["file"]
            library_name = library.get("label", fctl_file.stem)
            
            if library_name in existing_toolsets:
                toolset_map[library_name] = existing_toolsets[library_name]["id"]
                continue
            
            try:
                loaded_library = load_library_with_tools(library, tool_bits_dir)
                members = [
                    {
                        "tool_number": tool.get("nr"),
                        "tool_path": tool.get("path"),
                        "description": tool.get("tool_data", {}).get("name") if tool.get("resolved") else None
                    }
                    for tool in loaded_library.get("tools", [])
                ]
                toolset = {
                    "name": library_name,
                    "description": f"FreeCAD library: {library_name}",
                    "type": "template",
                    "members": members,
                    "status": "active",
                    "activation": {"source": "freecad", "library_file": fctl_file.name}
                }
                tool_sets.append(toolset)
                toolset_names.append(library_name)
            except Exception as e:
                errors += 1
                self.log_error(f"Failed to create tool set {library_name}: {e}")
        
        if tool_sets:
            try:
                response = requests.post(f"{url}/api/v1/tool-sets", headers=headers, json={"items": tool_sets}, timeout=30)
                response.raise_for_status()
                for idx, result in enumerate(response.json().get("results", [])):
                    toolset_map[toolset_names[idx]] = result["id"]
                for name in toolset_names:
                    self.form.statusText.append(f"✓ {name}")
            except Exception as e:
                errors += len(tool_sets)
                self.log_error(f"Tool set creation failed: {e}")
        
        # Create ToolPresets
        existing_presets = set()
        try:
            response = requests.get(f"{url}/api/v1/tool-presets", headers=headers, params={"machine_id": machine_id}, timeout=10)
            response.raise_for_status()
            for item in response.json().get("items", []):
                existing_presets.add((item.get("machine_id"), item.get("tool_number")))
        except Exception as e:
            self.log_error(f"Could not fetch presets: {e}")
        
        for item in selected_libraries:
            data = item.data(QtCore.Qt.UserRole)
            library = data["library"]
            library_name = library.get("label", data["file"].stem)
            try:
                loaded_library = load_library_with_tools(library, tool_bits_dir)
                for tool in loaded_library.get("tools", []):
                    tool_number = tool.get("nr")
                    if (machine_id, tool_number) in existing_presets:
                        self.form.statusText.append(f"⊙ Skipping existing preset: T{tool_number}")
                        continue
                    preset = {
                        "machine_id": machine_id,
                        "tool_number": tool_number,
                        "description": tool.get("tool_data", {}).get("name", f"{library_name} - T{tool_number}"),
                        "metadata": {
                            "source": "freecad",
                            "library_name": library_name,
                            "tool_path": tool.get("path", ""),
                            "fctb_file": tool.get("path", "")
                        }
                    }
                    tool_presets.append(preset)
            except Exception as e:
                errors += 1
                self.log_error(f"Failed to parse library {library_name}: {e}")
        
        if tool_presets:
            try:
                response = requests.post(f"{url}/api/v1/tool-presets", headers=headers, json={"items": tool_presets}, timeout=30)
                response.raise_for_status()
                libraries_exported += len(selected_libraries)
                self.form.statusText.append(f"✓ Exported {len(tool_presets)} tool presets")
            except Exception as e:
                errors += len(tool_presets)
                self.log_error(f"Preset export failed: {e}")
        
        return libraries_exported, errors
    
    def import_tool_items(self, url: str, headers: Dict, tool_bits_dir: Path, shapes_dir: Path) -> Tuple[int, int]:
        """Import selected tool items from Smooth."""
        import requests
        selected_items = self.form.toolItemsList.selectedItems()
        if not selected_items:
            return 0, 0
        
        imported, errors = 0, 0
        for idx, item in enumerate(selected_items):
            smooth_item = item.data(QtCore.Qt.UserRole)
            try:
                fctb_data = smooth_to_fctb(smooth_item)
                tool_id = smooth_item.get('freecad_metadata', {}).get('id', smooth_item.get('id', 'unknown'))
                filepath = tool_bits_dir / f"{tool_id}.fctb"
                with open(filepath, 'w') as f:
                    json.dump(fctb_data, f, indent=2)
                
                if shape_data := smooth_item.get('shape_data'):
                    try:
                        shape_file = download_and_save_shape(shape_data, shapes_dir)
                        if shape_file:
                            self.form.statusText.append(f"✓ Downloaded shape: {shape_file.name}")
                    except Exception as e:
                        self.log_error(f"Could not download shape: {e}")
                
                imported += 1
                self.form.statusText.append(f"✓ {smooth_item.get('description', 'Unknown')}")
            except Exception as e:
                errors += 1
                self.log_error(f"Failed to import {smooth_item.get('description', 'Unknown')}: {e}")
            
            self.form.progressBar.setValue(50 + int((idx + 1) / len(selected_items) * 25))
        
        return imported, errors
    
    def import_tool_sets(self, url: str, headers: Dict, tool_library_dir: Path) -> Tuple[int, int]:
        """Import selected tool sets as FreeCAD libraries."""
        import requests
        selected_sets = self.form.toolSetsList.selectedItems()
        if not selected_sets:
            return 0, 0
        
        imported, errors = 0, 0
        for idx, item in enumerate(selected_sets):
            toolset = item.data(QtCore.Qt.UserRole)
            try:
                library = {
                    "version": 1,
                    "label": toolset.get('name', 'unknown'),
                    "tools": [
                        {"nr": m.get('tool_number'), "path": m.get('tool_path')}
                        for m in toolset.get('members', [])
                        if m.get('tool_number') is not None and m.get('tool_path')
                    ]
                }
                filepath = tool_library_dir / f"{toolset.get('name', 'unknown')}.fctl"
                with open(filepath, 'w') as f:
                    json.dump(library, f, indent=2)
                imported += 1
                self.form.statusText.append(f"✓ {toolset.get('name')} ({len(library['tools'])} tools)")
            except Exception as e:
                errors += 1
                self.log_error(f"Failed to import tool set {toolset.get('name', 'Unknown')}: {e}")
            
            self.form.progressBar.setValue(75 + int((idx + 1) / len(selected_sets) * 25))
        
        return imported, errors
    
    def start_sync(self):
        """Orchestrate bidirectional sync."""
        import requests
        if not self.config.get("api_url"):
            self.log_error("Configure Smooth connection first")
            return
        
        if self.form.statusText:
            self.form.statusText.clear()
        if self.form.progressBar:
            self.form.progressBar.setValue(0)
        tool_bits_dir, tool_library_dir, shapes_dir = self.get_freecad_paths()
        headers = self.get_api_headers()
        url = self.config["api_url"].rstrip('/')
        current_tab = self.form.tabs.currentIndex()
        mode = "Export" if current_tab == 0 else "Import"
        self.form.statusText.append(f"Starting {mode} with Smooth...")
        App.Console.PrintMessage(f"=== Starting {mode} with Smooth ===\n")
        
        try:
            tools_exported, libraries_exported, imported_items, imported_sets, errors = 0, 0, 0, 0, 0
            if current_tab == 0:  # Export
                tools_exported, t_errors = self.export_tool_bits(url, headers, tool_bits_dir)
                libraries_exported, l_errors = self.export_libraries(url, headers, tool_bits_dir, tool_library_dir)
                errors += t_errors + l_errors
            else:  # Import
                imported_items, i_errors = self.import_tool_items(url, headers, tool_bits_dir, shapes_dir)
                imported_sets, s_errors = self.import_tool_sets(url, headers, tool_library_dir)
                errors += i_errors + s_errors
            
            self.form.progressBar.setValue(100)
            self.form.statusText.append(f"\n=== {mode} Complete ===")
            if current_tab == 0:
                self.form.statusText.append(f"Exported - Tools: {tools_exported}, Libraries: {libraries_exported}, Errors: {errors}")
                App.Console.PrintMessage(f"Exported: {tools_exported} tools, {libraries_exported} libraries, Errors: {errors}\n")
            else:
                self.form.statusText.append(f"Imported - Tools: {imported_items}, Libraries: {imported_sets}, Errors: {errors}")
                App.Console.PrintMessage(f"Imported: {imported_items} tools, {imported_sets} libraries, Errors: {errors}\n")
        except ImportError:
            self.log_error("requests library not installed")
        except Exception as e:
            self.log_error(f"Sync error: {e}")