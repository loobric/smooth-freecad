# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Loobric FreeCAD Addon - GUI initialization.

This addon provides bidirectional synchronization between FreeCAD CAM tool libraries
and the Loobric tool data exchange system.

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
    
    App.Console.PrintMessage("Initializing Loobric...\n")

    # Import siblings via the package namespace — FreeCAD loads this addon as
    # `freecad.Loobric`, so bare `import LoobricCommands` fails from a normal
    # install (part of issue #4). Fall back to flat imports only for a dev/legacy
    # layout where the package dir is on sys.path.
    try:
        from freecad.Loobric import LoobricCommands, LoobricPreferences
    except ImportError:
        import LoobricCommands
        import LoobricPreferences

    # Registering commands runs Gui.addCommand (on import of LoobricCommands).
    _ = LoobricCommands

    # Register preference page
    try:
        Gui.addPreferencePage(LoobricPreferences.LoobricPreferencePage, "CAM")
        App.Console.PrintMessage("Loobric preference page registered\n")
    except Exception as e:
        App.Console.PrintError(f"Failed to register preference page: {e}\n")
        import traceback
        traceback.print_exc()
    
    # Register workbench manipulator
    try:
        class LoobricManipulator:
            def modifyToolBars(self):
                return [{"append" : "Loobric_Sync", "toolBar" : "Tool Commands"},
                        {"append" : "Loobric_SetFromJob", "toolBar" : "Tool Commands"}]

            def modifyMenuBar(self):
                return [{"insert" : "Loobric_Sync", "menuItem" : "CAM_ToolBitDock", "after": ""},
                        {"insert" : "Loobric_SetFromJob", "menuItem" : "CAM_ToolBitDock", "after": ""},
                        {"insert" : "Loobric_AssetStore", "menuItem" : "CAM_ToolBitDock", "after": ""}]


        App.Console.PrintMessage("Creating workbench manipulator...\n")
        

        manipulator = LoobricManipulator()
        App.Console.PrintMessage("Registering workbench manipulator...\n")
        Gui.addWorkbenchManipulator(manipulator)
        App.Console.PrintMessage("Loobric workbench manipulator registered\n")
    except Exception as e:
        App.Console.PrintError(f"Failed to register workbench manipulator: {e}\n")
        import traceback
        traceback.print_exc()

    # Auto-activation of the asset store: when the CAM workbench comes up and
    # the preference is on, store mode starts by itself (storemode runs
    # camassets setup first, then the swap). FreeCAD < 1.1 or a disabled
    # preference: the hook is a no-op.
    try:
        try:
            from freecad.Loobric import storemode
        except ImportError:
            import storemode
        Gui.getMainWindow().workbenchActivated.connect(storemode.auto_activate)
        App.Console.PrintMessage("Loobric asset-store hook registered\n")
    except Exception as e:
        App.Console.PrintError(f"Failed to register asset-store hook: {e}\n")

    # The Loobric toolbar button doubles as the sync-status indicator — the
    # icon recolors: green (in sync), yellow (starting/syncing), red
    # (conflicts held for a decision), gray (offline). FreeCAD < 1.1 has no
    # asset system — no store mode, no coloring.
    try:
        try:
            from freecad.Loobric import LoobricStatusWidget
        except ImportError:
            import LoobricStatusWidget
        LoobricStatusWidget.install_badge()
    except ImportError:
        pass
    except Exception as e:
        App.Console.PrintError(f"Failed to install the Loobric status "
                               f"badge: {e}\n")

    App.Console.PrintMessage("Loobric addon initialized\n")


# Call initialize when module is loaded
App.Console.PrintMessage("Loading Loobric GUI...\n")
initialize()

