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
        class SmoothManipulator:
            def modifyToolBars(self):
                return [{"append" : "Smooth_Sync", "toolBar" : "Tool Commands"}]
                
            def modifyMenuBar(self):
                return [{"insert" : "Smooth_Sync", "menuItem" : "CAM_ToolBitDock", "after": ""}]


        App.Console.PrintMessage("Creating workbench manipulator...\n")
        

        manipulator = SmoothManipulator()
        App.Console.PrintMessage("Registering workbench manipulator...\n")
        Gui.addWorkbenchManipulator(manipulator)
        App.Console.PrintMessage("Smooth workbench manipulator registered\n")
    except Exception as e:
        App.Console.PrintError(f"Failed to register workbench manipulator: {e}\n")
        import traceback
        traceback.print_exc()
    
    App.Console.PrintMessage("Smooth addon initialized\n")


# Call initialize when module is loaded
App.Console.PrintMessage("Loading Smooth GUI...\n")
initialize()

