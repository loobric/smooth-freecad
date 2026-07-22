# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

# Namespace-package declaration for the FreeCAD `freecad.*` namespace. Every
# FreeCAD namespace addon ships this identical file so that FreeCAD can merge the
# addon's `freecad/Loobric` package into the shared `freecad` namespace. Without
# it, `freecad.Loobric` is not importable from a manual clone (or the Addon
# Manager) and the addon's init_gui.py never runs — the symptom in issue #4.
__path__ = __import__("pkgutil").extend_path(__path__, __name__)
