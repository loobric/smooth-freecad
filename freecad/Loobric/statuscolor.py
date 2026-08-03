# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""Map an asset-store state snapshot to the toolbar icon color.

The Loobric toolbar button IS the sync-health indicator: the whole icon is
recolored — green (in sync), yellow (activating or syncing), red (held
conflicts need a decision), gray (offline, working from the mirror) — and
keeps its normal brand color while store mode is off. :func:`tint_svg` does
the recoloring on the SVG source, so the shape stays crisp at any size and
no per-color icon files exist to drift out of sync with the artwork.

No FreeCAD or Qt imports; runs headless under pytest.
"""

# The single fill in Resources/icons/Loobric.svg. If the artwork ever
# changes color, this must follow it (test_statuscolor pins the two
# together against the real file).
BRAND_FILL = "#204a3e"

COLORS = {"green": "#2e9e4b", "yellow": "#f9a825",
          "red": "#e53935", "gray": "#9e9e9e"}


def status_color(state):
    """The color key for a state snapshot, or None for the plain icon.

    Held conflicts outrank everything — they need a decision whatever the
    worker is doing right now; a brief yellow flicker during a pass would
    only hide that.
    """
    status = state.get("status", "inactive")
    if status == "inactive":
        return None
    if state.get("held"):
        return "red"
    if status in ("activating", "syncing"):
        return "yellow"
    if status == "offline":
        return "gray"
    return "green"


def tint_svg(svg_text, color):
    """The icon's SVG source with the brand fill swapped for ``color``'s hex."""
    return svg_text.replace(BRAND_FILL, COLORS[color])
