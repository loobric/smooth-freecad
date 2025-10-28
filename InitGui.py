# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Smooth FreeCAD Addon - GUI initialization.

This addon provides bidirectional synchronization between FreeCAD CAM tool libraries
and the Smooth tool data exchange system.

This addon does NOT create a separate workbench. Instead it:
1. Adds a preference page to FreeCAD preferences
2. Adds a sync button to the CAM workbench toolbar
"""

import os
import FreeCAD as App
import FreeCADGui as Gui


def initialize():
    """Initialize the addon - register commands, preferences, and manipulator."""
    if not App.GuiUp:
        return
    
    App.Console.PrintMessage("Initializing Smooth...\n")
    
    # Import and register commands first
    import SmoothCommands
    
    # Register preference page
    try:
        import SmoothPreferences
        Gui.addPreferencePage(SmoothPreferences.SmoothPreferencePage, "CAM")
        App.Console.PrintMessage("Smooth preference page registered\n")
    except Exception as e:
        App.Console.PrintError(f"Failed to register preference page: {e}\n")
        import traceback
        traceback.print_exc()
    
    # Register workbench manipulator
    try:
        App.Console.PrintMessage("Creating workbench manipulator...\n")
        
        # Define manipulator class inside initialize to ensure proper scoping
        class SmoothManipulator:
            """Manipulator to add Smooth commands to FreeCAD menus and toolbars."""
            
            def modifyMenuBar(self):
                """No menu bar modifications.
                
                Smooth button is added to CAM workbench toolbar via activation callback.
                """
                return []
            
            def modifyToolBars(self):
                """No global toolbar modifications.
                
                Smooth button is added to CAM workbench toolbar via activation callback.
                """
                return []
            
            def modifyContextMenu(self, recipient):
                """No context menu modifications."""
                return []
        
        manipulator = SmoothManipulator()
        App.Console.PrintMessage("Registering workbench manipulator...\n")
        Gui.addWorkbenchManipulator(manipulator)
        App.Console.PrintMessage("Smooth workbench manipulator registered\n")
    except Exception as e:
        App.Console.PrintError(f"Failed to register workbench manipulator: {e}\n")
        import traceback
        traceback.print_exc()
    
    # Register callback for CAM workbench activation
    try:
        from PySide import QtGui, QtCore
        mw = Gui.getMainWindow()
        cam_toolbar_added = [False]  # Use list to allow modification in nested function
        
        def add_to_cam_toolbar():
            """Add Smooth button to CAM toolbar after a delay."""
            try:
                # Create a QAction for the Smooth_Sync command
                import SmoothCommands
                cmd = SmoothCommands.SmoothSyncCommand()
                resources = cmd.GetResources()
                
                smooth_action = QtGui.QAction(mw)
                smooth_action.setText(resources.get('MenuText', 'Sync with Smooth'))
                smooth_action.setToolTip(resources.get('ToolTip', ''))
                smooth_action.setObjectName("Smooth_Sync")
                
                # Set icon if available
                icon_path = resources.get('Pixmap', '')
                if icon_path and os.path.exists(icon_path):
                    smooth_action.setIcon(QtGui.QIcon(icon_path))
                
                # Connect to the command's Activated method
                smooth_action.triggered.connect(lambda: Gui.runCommand("Smooth_Sync"))
                
                App.Console.PrintMessage("Created Smooth_Sync action\n")
                
                # Find the "Helpful Tools" toolbar in CAM workbench
                toolbars = mw.findChildren(QtGui.QToolBar)
                target_toolbar = None
                for toolbar in toolbars:
                    if toolbar.windowTitle() == "Tool Commands":
                        target_toolbar = toolbar
                        break
                
                if target_toolbar:
                    # Check if our action is already in the toolbar
                    existing_actions = target_toolbar.actions()
                    for action in existing_actions:
                        if action.objectName() == "Smooth_Sync":
                            App.Console.PrintMessage("Smooth Sync button already exists in toolbar\n")
                            cam_toolbar_added[0] = True
                            return
                    
                    target_toolbar.addAction(smooth_action)
                    App.Console.PrintMessage("✓ Smooth Sync added to CAM Helpful Tools toolbar\n")
                    cam_toolbar_added[0] = True
                else:
                    App.Console.PrintWarning("Helpful Tools toolbar not found in CAM workbench\n")
            except Exception as e:
                App.Console.PrintWarning(f"Could not add to CAM toolbar: {e}\n")
                import traceback
                traceback.print_exc()
        
        def on_workbench_activated():
            wb = Gui.activeWorkbench()
            if wb and wb.__class__.__name__ == "CAMWorkbench" and not cam_toolbar_added[0]:
                # Use QTimer to add button after CAM workbench finishes initializing
                QtCore.QTimer.singleShot(100, add_to_cam_toolbar)
    
        mw.workbenchActivated.connect(on_workbench_activated)
        App.Console.PrintMessage("CAM workbench activation callback registered\n")
    except Exception as e:
        App.Console.PrintWarning(f"Could not register CAM workbench callback: {e}\n")
        import traceback
        traceback.print_exc()
    
    App.Console.PrintMessage("Smooth addon initialized\n")


# Call initialize when module is loaded
App.Console.PrintMessage("Loading Smooth GUI...\n")
initialize()

