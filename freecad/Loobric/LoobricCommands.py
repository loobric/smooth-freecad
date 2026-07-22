# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Loobric FreeCAD Addon - Command definitions.

Defines the Loobric sync command that gets added to the CAM workbench.
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui, QtCore
import os


class LoobricSyncCommand:
    """Command to sync tools with Loobric server.
    
    This command opens the sync dialog which allows:
    - Bidirectional sync (import/export)
    - Export only
    - Import only
    """
    
    def GetResources(self):
        # Try to load icon, fallback to default if not found
        icon_path = os.path.join(os.path.dirname(__file__), 'Resources', 'icons', 'Loobric.svg')
        
        return {
            'Pixmap': icon_path if os.path.exists(icon_path) else '',
            'MenuText': 'Loobric',
            'ToolTip': 'Open the Loobric window: sync tools, process the binding '
                       'inbox, and browse tools / sets / machines',
        }
    
    def Activated(self):
        """Execute when command is activated. The window is opened MODELESS so
        FreeCAD (and the CAM tool editors) stay usable beside it; a reference is
        kept on the command so the window isn't garbage-collected."""
        try:
            try:
                from freecad.Loobric import LoobricDialog
            except ImportError:
                import LoobricDialog  # flat layout on sys.path
            window = LoobricDialog.LoobricWindow()
            LoobricSyncCommand._window = window   # keep alive (modeless)
            window.setModal(False)
            window.show()
            window.raise_()
            window.activateWindow()
        except Exception as e:
            App.Console.PrintError(f"Failed to open Loobric window: {e}\n")
            # Show error to user
            from PySide import QtGui
            QtGui.QMessageBox.critical(
                None,
                "Loobric Error",
                f"Failed to open the Loobric window:\n\n{str(e)}"
            )
    
    def IsActive(self):
        """Return True if command should be active.
        
        The command is always available, but the dialog will check
        for proper configuration.
        """
        return True


class LoobricConfigureCommand:
    """Command to configure Loobric connection settings."""
    
    def GetResources(self):
        return {
            'Pixmap': '',
            'MenuText': 'Configure Loobric',
            'ToolTip': 'Configure Loobric server URL and API key'
        }
    
    def Activated(self):
        """Open the Loobric settings, which live on the CAM preference page
        (registered by init_gui via LoobricPreferences). There is no separate
        config dialog — the preference page is the single configuration UI."""
        try:
            try:
                Gui.showPreferences("CAM")
            except TypeError:
                # Older FreeCAD: showPreferences takes no group argument.
                Gui.showPreferences()
        except Exception as e:
            App.Console.PrintError(f"Failed to open Loobric settings: {e}\n")
            QtGui.QMessageBox.information(
                None, "Configure Loobric",
                "Open Edit → Preferences → CAM → Loobric to "
                "configure the server URL and API key.")
    
    def IsActive(self):
        """Return True if command should be active."""
        return True


# Register commands
Gui.addCommand('Loobric_Sync', LoobricSyncCommand())
Gui.addCommand('Loobric_Configure', LoobricConfigureCommand())

App.Console.PrintMessage("Loobric commands registered\n")
