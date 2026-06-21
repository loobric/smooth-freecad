# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Mapping between FreeCAD file formats and the Smooth **sectioned** tool schema
(docs/TOOL_SCHEMA.md). Pure functions, no FreeCAD imports — fully testable
headless.

The model in one line: every entity has three sections — server-owned
``internal``, provenance-tagged ``canonical`` (the agreed truth), and a map of
per-client sections each with an opaque, client-owned ``data`` payload. A
routine client *sync* writes only its own ``clients.freecad.data``; canonical
facts are changed deliberately through the **assert** door.

This module's job is the FreeCAD <-> sections translation:

- ``.fctb`` (a tool bit)     -> a **ToolInstanceRecord**.
  The full original document rides verbatim in ``clients.freecad.data.fctb``
  (lossless — unknown keys like FreeCAD's F&S "presets" survive). The
  shape/type and dimensions are surfaced as canonical **asserts** (FreeCAD is
  the client whose scope permits asserting ``geometry.shape``). Nothing is
  fabricated: a value we can't determine is simply not asserted.
- ``.fctl`` (a tool library) -> a **ToolSet**.
  Per-tool numbers are promoted OUT of the client section into canonical
  ``members`` (a set's numbering is shared truth, §7.4); the FreeCAD label and
  format version stay in ``clients.freecad.data``.
- Identity: after first contact the server's ``internal.id`` is persisted
  client-side as the additive ``smooth.record_id`` key in the ``.fctb``/``.fctl``
  (older readers ignore it). ``client_item_id`` (the ``.fctb`` id / ``.fctl``
  label) is the re-adoption fallback the server holds.

Lossless regeneration rule: a parameter string from the original file is kept
verbatim unless the canonical value actually differs — so formatting quirks
("3.175 mm") never churn.
"""
import copy
import re
from collections import namedtuple

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

# FreeCAD CAM tool shapes, keyed by the lowercased shape-type (which is also
# what canonical geometry.shape stores). Each entry is (shape_file, shape_type),
# mirrored verbatim from FreeCAD's authoritative model definitions
# (Mod/CAM/Path/Tool/{toolbit,shape}/models). FreeCAD resolves a bit's TYPE and
# full parameter schema from the `shape` file's id (the .fcstd stem) — so all
# that a synthesized .fctb must get right is the exact file and shape-type; the
# shape asset supplies the schema and sensible defaults, which the record's
# geometry then overlays. Note FreeCAD's own irregular names: type "VBit" with
# file "v-bit.fcstd", "ThreadMill" with "thread-mill.fcstd", "TaperedBallNose".
SHAPE_DEFS = {
    "endmill": ("endmill.fcstd", "Endmill"),
    "ballend": ("ballend.fcstd", "Ballend"),
    "bullnose": ("bullnose.fcstd", "Bullnose"),
    "chamfer": ("chamfer.fcstd", "Chamfer"),
    "dovetail": ("dovetail.fcstd", "Dovetail"),
    "drill": ("drill.fcstd", "Drill"),
    "probe": ("probe.fcstd", "Probe"),
    "radius": ("radius.fcstd", "Radius"),
    "reamer": ("reamer.fcstd", "Reamer"),
    "slittingsaw": ("slittingsaw.fcstd", "SlittingSaw"),
    "tap": ("tap.fcstd", "Tap"),
    "taperedballnose": ("taperedballnose.fcstd", "TaperedBallNose"),
    "threadmill": ("thread-mill.fcstd", "ThreadMill"),
    "vbit": ("v-bit.fcstd", "VBit"),
}
DEFAULT_SHAPE = "endmill"
FREECAD_SHAPES = list(SHAPE_DEFS)

# The client identity this module maps to (the `clients` map key) and the
# default actor stamped on its assertions.
CLIENT_NAME = "freecad"

# Best-effort tool-type guess from a tool's NAME, mirroring FreeCAD's own
# guess_subclass_from_name. A record's stored shape is unreliable (machine-
# sourced tools were stamped a wrong 'endmill'), but the name almost always
# says what the tool is — so this pre-selects the import type picker, turning
# "set the type on all 17 tools" into "accept most, fix a couple". Ordered so
# more specific keywords win (tapered before ball/tap; ball before flute).
_SHAPE_NAME_HINTS = [
    (("tapered ball", "taperedball", "tapered"), "taperedballnose"),
    (("thread",), "threadmill"),
    (("slitting", "slit saw", "slitsaw"), "slittingsaw"),
    (("dovetail",), "dovetail"),
    (("chamfer",), "chamfer"),
    (("v-bit", "vbit", "v bit", "engrav"), "vbit"),
    (("ball",), "ballend"),
    (("bull",), "bullnose"),
    (("probe",), "probe"),
    (("reamer",), "reamer"),
    (("radius", "corner round", "roundover"), "radius"),
    (("drill",), "drill"),
    (("tap",), "tap"),
    (("endmill", "end mill", "flute"), "endmill"),
]


def guess_shape_from_name(name):
    """Guess a tool shape key from a tool name, or None if nothing matches."""
    text = (name or "").lower()
    for keywords, shape in _SHAPE_NAME_HINTS:
        if any(k in text for k in keywords):
            return shape
    return None


def fctb_shape(fctb_doc):
    """The shape a parsed .fctb document declares (lowercased stem), or None.

    Note this is unreliable as a *type*: tools that reached the server without
    a FreeCAD origin were historically stamped 'endmill' by an earlier import,
    so the user must be able to override/correct it on import."""
    shape_type = (fctb_doc or {}).get("shape-type")
    return str(shape_type).lower() if shape_type else None


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


def _field_value(field):
    """Read the ``value`` out of a provenance-tagged canonical Field.

    Accepts a raw value too, so callers can be lenient about whether a number
    arrived already wrapped ({"value": .., "source": ..}) or bare."""
    if isinstance(field, dict):
        return field.get("value")
    return field


# ---------------------------------------------------------------------------
# .fctb <-> ToolInstanceRecord
# ---------------------------------------------------------------------------

# The pieces a client sends to materialize a ToolInstanceRecord:
#   data           -> the clients.freecad.data payload (lossless .fctb)
#   client_item_id -> the envelope's re-adoption handle
#   asserts        -> the canonical facts to declare, as (path, value) tuples
InstanceSections = namedtuple(
    "InstanceSections", ["data", "client_item_id", "asserts"])


def _geometry_asserts(params):
    """Canonical geometry asserts (path, value) extracted from .fctb params."""
    out = []
    for param, key in QUANTITY_PARAMS.items():
        if param in params:
            value, _unit = parse_quantity(params[param])
            if value is not None:
                out.append(("geometry." + key, value))
    for param, key in INT_PARAMS.items():
        if param in params:
            try:
                out.append(("geometry." + key, int(params[param])))
            except (TypeError, ValueError):
                pass
    return out


def fctb_record_id(fctb_doc):
    """The server record id a prior export wrote into the .fctb, or None.

    This is the client's private UPDATE-vs-CREATE bookkeeping (the additive
    'smooth' key), not part of any wire section."""
    return ((fctb_doc or {}).get("smooth") or {}).get("record_id")


def record_to_instance_sections(fctb_doc, shape=None, client_item_id=None):
    """Translate a parsed .fctb into the pieces a client sends for a
    ToolInstanceRecord.

    Returns an ``InstanceSections(data, client_item_id, asserts)``:

    - ``data`` = ``{"fctb": <doc>}`` — the full original document, lossless and
      opaque, minus the additive 'smooth' identity key (plumbing, not tool
      data). Unknown keys (presets, attribute, …) ride along untouched.
    - ``client_item_id`` = the .fctb ``id`` (or the caller-supplied filename
      stem) — the envelope's re-adoption fallback.
    - ``asserts`` = the canonical facts FreeCAD declares, as ``(path, value)``
      tuples: ``name``, ``geometry.shape`` (chosen, or read from the file, or
      guessed from the name — and simply omitted if none of those yield one, so
      the field stays honestly ``unknown`` rather than a fabricated 'endmill'),
      and each present ``geometry.*`` dimension.

    ``shape`` is the explicit user choice on import (the type picker); when set
    it overrides the file's own shape-type, which is the "correct the wrongly
    stamped type" path.
    """
    doc = copy.deepcopy(fctb_doc)
    doc.pop("smooth", None)

    name = doc.get("name") or doc.get("id") or "unnamed tool"
    chosen_shape = shape or fctb_shape(doc) or guess_shape_from_name(name)

    asserts = [("name", name)]
    if chosen_shape:
        asserts.append(("geometry.shape", chosen_shape))
    asserts.extend(_geometry_asserts(doc.get("parameter") or {}))

    item_id = client_item_id or fctb_doc.get("id")
    return InstanceSections(data={"fctb": doc},
                            client_item_id=item_id,
                            asserts=asserts)


def instance_to_fctb(record):
    """Regenerate a .fctb document from a sectioned ToolInstanceRecord.

    Strategy (lossless first, canonical wins on conflict):

    - Prefer the verbatim ``clients.freecad.data.fctb`` as the base, so unknown
      keys survive untouched.
    - The canonical ``geometry.shape`` is authoritative for the *type*. When it
      differs from the base document's shape-type the bit is REBUILT from
      ``SHAPE_DEFS`` (a base whose type was a wrong 'endmill' must not be
      reused — FreeCAD fixes a bit's shape at creation, so the corrected type
      is the one moment to get the shape FILE and TYPE right). When the
      canonical shape matches (or there is none), the base is preserved.
    - Server-canonical edits overlay: ``name`` always; each ``geometry.*``
      parameter only when the canonical value actually differs from the
      original string (lossless rule — formatting never churns).
    - The additive ``smooth`` key records ``internal.id`` for the next export.
    """
    internal = record.get("internal") or {}
    canonical = record.get("canonical") or {}
    section = (record.get("clients") or {}).get(CLIENT_NAME) or {}
    base = (section.get("data") or {}).get("fctb")
    geometry = canonical.get("geometry") or {}

    canon_shape = _field_value(geometry.get("shape"))
    canon_shape = str(canon_shape).lower() if canon_shape else None
    base_shape = fctb_shape(base) if base else None

    # A canonical shape that disagrees with the base is a correction: discard
    # the (often wrongly-endmill) base document and synthesize the right type.
    rebuild = canon_shape is not None and canon_shape != base_shape

    if base and not rebuild:
        doc = copy.deepcopy(base)
    else:
        chosen = canon_shape or base_shape or DEFAULT_SHAPE
        shape_file, shape_type = SHAPE_DEFS.get(
            chosen, (chosen + ".fcstd", chosen.capitalize()))
        doc = {
            "version": 2,
            "name": "",
            "shape": shape_file,
            "shape-type": shape_type,
            "attribute": {},
            # FreeCAD fills the shape's schema/defaults from the shape file;
            # the canonical geometry (Diameter, …) overlays below.
            "parameter": {},
        }

    name = _field_value(canonical.get("name"))
    if name is not None:
        doc["name"] = name
    doc.setdefault("name", "")

    params = doc.setdefault("parameter", {})
    for param, key in QUANTITY_PARAMS.items():
        value = _field_value(geometry.get(key))
        if value is None:
            continue
        unit = (geometry.get(key) or {}).get("unit") if isinstance(
            geometry.get(key), dict) else None
        original_value, original_unit = parse_quantity(params.get(param, ""))
        if original_value is not None and abs(original_value - value) < 1e-9:
            continue  # unchanged: keep the original string verbatim
        params[param] = format_quantity(value, original_unit or unit or "mm")
    for param, key in INT_PARAMS.items():
        value = _field_value(geometry.get(key))
        if value is not None and params.get(param) != value:
            params[param] = int(value)

    doc["smooth"] = {"record_id": internal.get("id"),
                     "version": internal.get("version")}
    return doc


def catalog_to_fctb(catalog_record, instance_record):
    """Synthesize a .fctb for a tool just created from a catalog record.

    Two sectioned records meet here:

    - ``catalog_record`` (a ToolCatalogRecord) holds the usable NOMINAL geometry
      and name in its ``canonical`` section — the shape and dimensions the local
      bit must take.
    - ``instance_record`` (the ToolInstanceRecord the catalog->instance door just
      created) carries the new ``internal.id`` the local tool must track, but its
      OWN ``canonical.geometry`` is deliberately EMPTY: at creation the physical
      tool has not been measured, so its measured geometry is unknown. The usable
      shape therefore has to come from the catalog's nominal geometry, not the
      instance.

    Built by reusing :func:`instance_to_fctb` on a merged record: the instance's
    ``internal`` (so ``smooth.record_id`` is the NEW instance's id), the catalog's
    canonical geometry (the nominal shape + dimensions), and the instance's own
    name when it set one (a ``--name`` override) else the catalog's name. Empty
    ``clients`` — there is no prior .fctb to preserve. Pure; inputs untouched."""
    catalog = catalog_record or {}
    instance = instance_record or {}
    merged_canonical = copy.deepcopy(catalog.get("canonical") or {})
    inst_name = (instance.get("canonical") or {}).get("name")
    if _field_value(inst_name) is not None:
        merged_canonical["name"] = copy.deepcopy(inst_name)
    merged = {
        "internal": instance.get("internal") or {},
        "canonical": merged_canonical,
        "clients": {},
    }
    return instance_to_fctb(merged)


# ---------------------------------------------------------------------------
# .fctl <-> ToolSet
# ---------------------------------------------------------------------------

# The pieces a client sends to materialize a ToolSet:
#   data           -> the clients.freecad.data payload (label + format version)
#   client_item_id -> the envelope's re-adoption handle
#   members        -> [{tool_record_id, number}] for the /members endpoint
#   asserts        -> canonical facts to declare, as (path, value) tuples
#   unresolved     -> .fctb paths with no known record id (reported, not dropped)
SetSections = namedtuple(
    "SetSections", ["data", "client_item_id", "members", "asserts",
                    "unresolved"])


def fctl_record_id(fctl_doc):
    """The server ToolSet id a prior export wrote into the .fctl, or None."""
    return ((fctl_doc or {}).get("smooth") or {}).get("record_id")


def fctl_to_set_sections(fctl_doc, record_id_by_path, client_item_id=None):
    """Translate a parsed .fctl (a FreeCAD tool library) into the pieces a
    client sends for a ToolSet.

    Args:
        fctl_doc: parsed ``{"label", "tools": [{"nr", "path"}], "version"}``.
        record_id_by_path: ``.fctb`` path -> server record id (from the bit
            export that must precede library export).
        client_item_id: re-adoption handle; defaults to the library label.

    Returns ``SetSections(data, client_item_id, members, asserts, unresolved)``:

    - ``data`` = ``{"fctl_label", "version"}`` — the FreeCAD-specific bits that
      aren't canonical, opaque in the client section.
    - ``members`` = ordered ``[{"tool_record_id", "number"}]`` — the per-tool
      numbers, promoted OUT of the client section into canonical membership
      (a set's numbering is shared truth, §7.4). Sent to the ``/members`` door.
    - ``asserts`` = ``[("name", <label>)]`` (FreeCAD claims the set's name).
    - ``unresolved`` = paths whose ``.fctb`` was never exported — reported, not
      silently dropped.
    """
    doc = copy.deepcopy(fctl_doc)
    doc.pop("smooth", None)
    label = doc.get("label") or "library"

    members = []
    unresolved = []
    for tool in (doc.get("tools") or []):
        path = tool.get("path")
        record_id = record_id_by_path.get(path)
        if record_id is None:
            unresolved.append(path)
            continue
        members.append({"tool_record_id": record_id, "number": tool.get("nr")})

    data = {"fctl_label": label, "version": doc.get("version", 1)}
    asserts = [("name", label)]
    item_id = client_item_id or label
    return SetSections(data=data, client_item_id=item_id, members=members,
                       asserts=asserts, unresolved=unresolved)


def set_to_fctl(toolset_record, path_by_record_id):
    """Regenerate a .fctl document (FreeCAD tool library) from a sectioned
    ToolSet.

    - Membership order and numbers come from canonical ``members`` (the shared
      truth); a member with no canonical number gets the next free ``nr``.
    - The label comes from canonical ``name``, falling back to the FreeCAD
      label stashed in ``clients.freecad.data``.
    - Members whose record has no known ``.fctb`` path are returned in
      ``unresolved`` for the caller to export first — never silently dropped.
    - The additive ``smooth`` key records ``internal.id`` for the next export.
    """
    internal = toolset_record.get("internal") or {}
    canonical = toolset_record.get("canonical") or {}
    section = (toolset_record.get("clients") or {}).get(CLIENT_NAME) or {}
    data = section.get("data") or {}

    label = (_field_value(canonical.get("name"))
             or data.get("fctl_label") or "library")
    members = canonical.get("members") or []

    used = {n for n in (_field_value(m.get("number")) for m in members)
            if isinstance(n, int)}
    next_nr = max(used) + 1 if used else 1

    tools = []
    unresolved = []
    for member in members:
        record_id = member.get("tool_record_id")
        path = path_by_record_id.get(record_id)
        if path is None:
            unresolved.append(record_id)
            continue
        nr = _field_value(member.get("number"))
        if not isinstance(nr, int):
            nr = next_nr
            next_nr += 1
        tools.append({"nr": nr, "path": path})

    doc = {
        "label": label,
        "tools": tools,
        "version": data.get("version", 1),
    }
    doc["smooth"] = {"record_id": internal.get("id"),
                     "version": internal.get("version")}
    return doc, unresolved
