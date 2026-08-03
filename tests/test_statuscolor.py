# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""Tests for the toolbar icon color mapping (statuscolor).

The recolored icon is the at-a-glance sync health: brand color when store
mode is off, yellow while starting or syncing, red the moment anything is
held (whatever else is going on — a decision is owed), gray offline, green
in sync. tint_svg does the recoloring on the SVG source, so BRAND_FILL must
match the shipped artwork — pinned here against the real file.
"""
from pathlib import Path

from freecad.Loobric.statuscolor import (BRAND_FILL, COLORS, status_color,
                                         tint_svg)

ICON = (Path(__file__).parent.parent / "freecad" / "Loobric" / "Resources"
        / "icons" / "Loobric.svg")


def test_inactive_means_the_plain_icon():
    assert status_color({"status": "inactive"}) is None
    assert status_color({}) is None                  # missing status = inactive


def test_idle_and_synced_is_green():
    assert status_color({"status": "idle", "held": 0}) == "green"


def test_activating_and_syncing_are_yellow():
    assert status_color({"status": "activating", "held": 0}) == "yellow"
    assert status_color({"status": "syncing", "held": 0}) == "yellow"


def test_held_conflicts_are_red_whatever_the_worker_does():
    assert status_color({"status": "idle", "held": 2}) == "red"
    assert status_color({"status": "syncing", "held": 1}) == "red"
    assert status_color({"status": "offline", "held": 1}) == "red"


def test_offline_without_conflicts_is_gray():
    assert status_color({"status": "offline", "held": 0}) == "gray"


def test_every_color_has_a_hex_value():
    for state in ({"status": "idle"}, {"status": "syncing"},
                  {"status": "offline"}, {"status": "idle", "held": 1}):
        assert status_color(state) in COLORS


def test_brand_fill_matches_the_shipped_artwork():
    assert BRAND_FILL in ICON.read_text()


def test_tint_svg_recolors_the_whole_icon():
    svg = ICON.read_text()
    tinted = tint_svg(svg, "red")
    assert BRAND_FILL not in tinted
    assert COLORS["red"] in tinted
    # only the fill changed — same document otherwise
    assert tinted.replace(COLORS["red"], BRAND_FILL) == svg
