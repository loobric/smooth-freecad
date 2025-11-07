# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Smooth FreeCAD Addon - Command definitions.

Defines the Smooth sync command that gets added to the CAM workbench.
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui, QtCore
import os


class SmoothSyncCommand:
    """Command to sync tools with Smooth server.
    
    This command opens the sync dialog which allows:
    - Bidirectional sync (import/export)
    - Export only
    - Import only
    """
    
    def GetResources(self):
        # Try to load icon, fallback to default if not found
        icon_path = os.path.join(os.path.dirname(__file__), 'Resources', 'icons', 'Smooth.svg')
        
        return {
            'Pixmap': icon_path if os.path.exists(icon_path) else '',
            'MenuText': 'Sync with Smooth',
            'ToolTip': 'Synchronize FreeCAD tool libraries with Smooth server',
        }
    
    def Activated(self):
        """Execute when command is activated."""
        try:
            import SmoothDialog
            dialog = SmoothDialog.SmoothSyncDialog()
            dialog.exec_()
        except Exception as e:
            App.Console.PrintError(f"Failed to open Smooth sync dialog: {e}\n")
            # Show error to user
            from PySide import QtGui
            QtGui.QMessageBox.critical(
                None,
                "Smooth Sync Error",
                f"Failed to open sync dialog:\n\n{str(e)}"
            )
    
    def IsActive(self):
        """Return True if command should be active.
        
        The command is always available, but the dialog will check
        for proper configuration.
        """
        return True


class SmoothConfigureCommand:
    """Command to configure Smooth connection settings."""
    
    def GetResources(self):
        return {
            'Pixmap': '',
            'MenuText': 'Configure Smooth',
            'ToolTip': 'Configure Smooth server URL and API key'
        }
    
    def Activated(self):
        """Execute when command is activated."""
        try:
            import SmoothDialog
            dialog = SmoothDialog.SmoothConfigDialog()
            dialog.exec_()
        except Exception as e:
            App.Console.PrintError(f"Failed to open Smooth config dialog: {e}\n")
    
    def IsActive(self):
        """Return True if command should be active."""
        return True


# Register commands
Gui.addCommand('Smooth_Sync', SmoothSyncCommand())
Gui.addCommand('Smooth_Configure', SmoothConfigureCommand())

App.Console.PrintMessage("Smooth commands registered\n")
