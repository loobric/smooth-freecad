# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
FreeCAD addon for Smooth tool data synchronization.

This is a reference implementation that validates Smooth's API design
and provides a working example for other CAM integrations.

File Formats:
- .fctb - Tool bit definitions (JSON-based)
- .fctl - Tool library collections (JSON-based)
- .fcstd - Custom tool shapes (FreeCAD documents)
"""

import FreeCAD as App
App.Console.PrintMessage("Loading Smooth addon (Init.py)...\n")