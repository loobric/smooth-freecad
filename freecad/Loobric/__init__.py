# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
FreeCAD addon for Loobric tool data synchronization.

This is a reference implementation that validates Loobric's API design
and provides a working example for other CAM integrations.

File Formats:
- .fctb - Tool bit definitions (JSON-based)
- .fctl - Tool library collections (JSON-based)
- .fcstd - Custom tool shapes (FreeCAD documents)

The pure modules in this package (mapping, client, sync) deliberately have
no FreeCAD dependency so they can be tested headless; only the GUI modules
import FreeCAD, and this __init__ must not force it.
"""

try:
    import FreeCAD as App
    App.Console.PrintMessage("Loading Loobric addon (Init.py)...\n")
except ImportError:
    # Headless context (tests, CI): the pure modules work without FreeCAD.
    App = None
