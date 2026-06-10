# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Mapping between FreeCAD file formats and the Smooth v2 facade.

Pure functions, no FreeCAD imports — fully testable headless. This module
implements the v2 client contract (smooth-freecad#5):

- .fctb (tool bit)  <-> ToolRecord, lossless: the full original document
  travels in ToolRecord.extra["freecad"]["fctb"], so keys this client
  doesn't model (notably FreeCAD's additive F&S "presets" key) survive
  the round trip untouched.
- .fctl (library)   <-> Library, lossless: per-tool numbers (nr) and the
  label travel in Library.extra["freecad"].
- Identity: after first export the server record id is written into the
  .fctb as an additive top-level "smooth" key (older readers ignore it,
  same mechanism FreeCAD's presets use). NO name-matching heuristics on
  the CAM side, ever.

Lossless rule for regeneration: a parameter string from the original file
is kept verbatim unless the server-side canonical value actually differs —
so formatting quirks ("3.175 mm") never churn.
"""
import copy
import re

# parameter name -> canonical geometry key (quantity-valued)
QUANTITY_PARAMS = {
    "Diameter": "diameter",
    "CuttingEdgeHeight": "cutting_edge_height",
    "Length": "length",
    "ShankDiameter": "shank_diameter",
}
# parameter name -> canonical geometry key (plain int)
INT_PARAMS = {
    "Flutes": "flutes",
}

_QUANTITY_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*(\S*)\s*$")


def parse_quantity(value):
    """Parse a FreeCAD quantity string like '6.00 mm' -> (6.0, 'mm').

    Returns (None, None) for non-quantity strings.
    """
    if isinstance(value, (int, float)):
        return float(value), ""
    match = _QUANTITY_RE.match(str(value))
    if not match:
        return None, None
    return float(match.group(1)), match.group(2)


def format_quantity(value, unit):
    """Render a canonical value as a FreeCAD-style quantity string.

    Used only when the value CHANGED server-side; trims trailing zeros so
    3.175 stays 3.175 while 6.0 renders as '6.00 mm' (FreeCAD's style).
    """
    text = f"{value:.4f}".rstrip("0")
    if text.endswith("."):
        text += "00"
    elif len(text.split(".")[1]) == 1:
        text += "0"
    return f"{text} {unit}".strip()


# ---------------------------------------------------------------------------
# .fctb <-> ToolRecord
# ---------------------------------------------------------------------------

def fctb_to_record(fctb):
    """Map a parsed .fctb document to a ToolRecord payload.

    Returns (payload, record_id): record_id is the server id from a prior
    export (the additive 'smooth' key), or None for a never-synced bit.

    Assumptions:
    - geometry carries the canonical keys the server needs (binding
      heuristics use geometry.diameter); everything else rides verbatim
      in extra["freecad"]["fctb"]
    - the 'smooth' key itself is stripped from the stored copy (it's
      identity plumbing, not tool data)
    """
    doc = copy.deepcopy(fctb)
    smooth_meta = doc.pop("smooth", None) or {}

    geometry = {}
    shape_type = doc.get("shape-type")
    if shape_type:
        geometry["shape"] = str(shape_type).lower()
    params = doc.get("parameter", {}) or {}
    for param, key in QUANTITY_PARAMS.items():
        if param in params:
            value, unit = parse_quantity(params[param])
            if value is not None:
                geometry[key] = value
                if unit:
                    geometry[key + "_unit"] = unit
    for param, key in INT_PARAMS.items():
        if param in params:
            try:
                geometry[key] = int(params[param])
            except (TypeError, ValueError):
                pass

    payload = {
        "name": doc.get("name") or doc.get("id") or "unnamed tool",
        "geometry": geometry,
        "extra": {"freecad": {"fctb": doc}},
    }
    return payload, smooth_meta.get("record_id")


def record_to_fctb(record):
    """Regenerate a .fctb document from a ToolRecord.

    Assumptions:
    - The verbatim original in extra["freecad"]["fctb"] is the base, so
      unknown keys (presets, attribute, ...) survive untouched
    - Server-side canonical edits overlay: name always; quantity/int
      parameters only when the canonical value differs from the original
      (lossless rule — formatting never churns)
    - The additive 'smooth' key records identity for the next export
    - Records that never came from FreeCAD get a minimal document built
      from geometry
    """
    base = (record.get("extra") or {}).get("freecad", {}).get("fctb")
    geometry = record.get("geometry") or {}

    if base:
        doc = copy.deepcopy(base)
    else:
        doc = {
            "version": 2,
            "name": record.get("name", ""),
            "shape": (geometry.get("shape", "endmill")) + ".fcstd",
            "shape-type": geometry.get("shape", "endmill").capitalize(),
            "attribute": {},
            "parameter": {},
        }

    doc["name"] = record.get("name", doc.get("name", ""))
    params = doc.setdefault("parameter", {})

    for param, key in QUANTITY_PARAMS.items():
        if key not in geometry:
            continue
        canonical = geometry[key]
        unit = geometry.get(key + "_unit", "mm")
        original_value, original_unit = parse_quantity(params.get(param, ""))
        if original_value is not None and abs(original_value - canonical) < 1e-9:
            continue  # unchanged: keep the original string verbatim
        params[param] = format_quantity(canonical, original_unit or unit)
    for param, key in INT_PARAMS.items():
        if key in geometry and params.get(param) != geometry[key]:
            params[param] = int(geometry[key])

    doc["smooth"] = {
        "record_id": record["id"],
        "version": record.get("version"),
    }
    return doc


# ---------------------------------------------------------------------------
# .fctl <-> Library
# ---------------------------------------------------------------------------

def fctl_to_library(fctl, record_id_by_path):
    """Map a parsed .fctl document to a Library payload.

    Args:
        fctl: parsed .fctl ({"label", "tools": [{"nr", "path"}], "version"})
        record_id_by_path: .fctb path -> server record id (from the bit
            export that must precede library export)

    Returns (payload, unresolved_paths): paths with no record id are
    reported, never silently dropped.
    """
    tools = fctl.get("tools", []) or []
    record_ids = []
    numbers = {}
    unresolved = []
    for tool in tools:
        path = tool.get("path")
        record_id = record_id_by_path.get(path)
        if record_id is None:
            unresolved.append(path)
            continue
        record_ids.append(record_id)
        numbers[record_id] = tool.get("nr")

    payload = {
        "name": fctl.get("label") or "library",
        "tool_record_ids": record_ids,
        "extra": {"freecad": {
            "label": fctl.get("label"),
            "version": fctl.get("version", 1),
            "numbers": numbers,
        }},
    }
    return payload, unresolved


def library_to_fctl(library, path_by_record_id):
    """Regenerate a .fctl document from a Library.

    Assumptions:
    - Membership order comes from tool_record_ids (the canonical list)
    - Tool numbers come from extra["freecad"]["numbers"]; members added
      server-side without a number get the next free nr
    - Members with no known .fctb path are returned in `unresolved` for
      the caller to export first
    """
    freecad_meta = (library.get("extra") or {}).get("freecad", {})
    numbers = dict(freecad_meta.get("numbers") or {})
    used = {n for n in numbers.values() if isinstance(n, int)}
    next_nr = max(used) + 1 if used else 1

    tools = []
    unresolved = []
    for record_id in library.get("tool_record_ids", []):
        path = path_by_record_id.get(record_id)
        if path is None:
            unresolved.append(record_id)
            continue
        nr = numbers.get(record_id)
        if not isinstance(nr, int):
            nr = next_nr
            next_nr += 1
        tools.append({"nr": nr, "path": path})

    doc = {
        "label": library.get("name", freecad_meta.get("label") or "library"),
        "tools": tools,
        "version": freecad_meta.get("version", 1),
    }
    return doc, unresolved
