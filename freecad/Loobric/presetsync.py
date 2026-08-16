# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""Promote FreeCAD F&S presets through Loobric's contribution door.

The server's cutting-data-preset design (loobric-server docs/PRESETS.md,
ratified 2026-08-16) makes translation the CLIENT's job: the sync lane stays
pure passthrough (the raw ``presets`` key keeps riding the ``.fctb`` in the
client section, lossless as ever), and this module additionally translates
each native preset into the ratified normal form and contributes it — the
same promotion pattern ``.fctl`` tool numbers already follow.

The native shape is FreeCAD's own (PR #30078, ``FeedsSpeeds/presets.py``):

    {"name": str|None, "material_hint": {"uuid","name"}|None,
     "op_type_hint": str|None, "surface_speed": float|None,   # m/min
     "chipload": float|None,                                  # mm/tooth
     "vert_feed_ratio": float}                                # default 0.33

Rules honored here:
- Identity is ``(origin="freecad", label)``; the label is the preset's
  ``name``, else the same ``material / op`` summary FreeCAD's UI derives —
  deterministic, so re-syncs replace-own instead of duplicating.
- The floor: material name + at least one engineering value. A preset with
  no material hint (or a UUID-only hint) CANNOT be normalized — it stays
  client-section-only, counted, never guessed.
- FreeCAD's op vocabulary maps into the server's ratified enum; the
  verbatim FreeCAD op always rides the extras bag, and an unmappable one
  (``surface_finish``) is honestly absent from ``op_type``.
- Units are fixed by the native schema: surface_speed is m/min, chipload
  is mm/tooth.
- Presets deleted in FreeCAD: their freecad-origin server entries are
  pruned when the key can (the delete door); a key that can't gets a log
  line, not a failure — a human can clean up in the Web UI.

No FreeCAD imports; runs headless under pytest.
"""

ORIGIN = "freecad"

# Marker key on a NATIVE preset dict that says "this entry was materialized
# from the server's union, it is not FreeCAD's own": promotion skips it, the
# prune ignores it, and regeneration refreshes it from the union. Editing
# such a preset in FreeCAD rebuilds the dict without the marker — the edit
# FORKS it into a user-owned freecad preset, which is exactly right: the
# moment you change the manufacturer's numbers they become your recipe.
EXTERNAL_KEY = "loobric_external"

# FreeCAD op vocabulary (FeedsSpeeds.types.OP_TYPES) -> the server's ratified
# enum. ``surface_finish`` has no server counterpart yet: op_type stays
# absent and the verbatim value rides extras.
OP_MAP = {
    "profile": "profiling",
    "pocket": "pocketing",
    "slot": "slotting",
    "drill": "drilling",
    "adaptive": "adaptive",
}

VC_UNIT = "m/min"
FZ_UNIT = "mm"

# The reverse maps, for materializing server entries into the native shape.
OP_MAP_REVERSE = {loobric: freecad for freecad, loobric in OP_MAP.items()}

# Unit -> factor into the native fixed units (m/min, mm/tooth). A unit not
# listed here is NOT guessed: the value is dropped and the entry carries
# whatever else translated.
_VC_FACTORS = {"m/min": 1.0, "mmin": 1.0, "m/min.": 1.0,
               "sfm": 0.3048, "ft/min": 0.3048, "fpm": 0.3048}
_FZ_FACTORS = {"mm": 1.0, "mm/tooth": 1.0,
               "in": 25.4, "inch": 25.4, "in/tooth": 25.4}


def _convert(leaf, factors, default_unit):
    """A server {value, unit} leaf -> native float, or None (never guessed)."""
    if not isinstance(leaf, dict):
        return None
    value, unit = leaf.get("value"), leaf.get("unit") or default_unit
    if not isinstance(value, (int, float)):
        return None
    factor = factors.get(str(unit).strip().lower())
    return value * factor if factor is not None else None


def derive_label(preset):
    """The preset's identity label: its ``name``, else FreeCAD's own
    ``material / op`` summary (mirrors FeedsSpeeds.presets.derive_preset_label
    so what the user sees in FreeCAD is what the server entry is called)."""
    name = preset.get("name")
    if name:
        return str(name)
    hint = preset.get("material_hint")
    mat = (hint.get("name") or hint.get("uuid") or "any") \
        if isinstance(hint, dict) else "any"
    op = preset.get("op_type_hint") or "any"
    if mat == "any" and op == "any":
        return "(unnamed)"
    return "%s / %s" % (mat, op)


def to_native(entry):
    """Materialize one server union entry as a native FreeCAD preset dict,
    marked external. The name is prefixed with the origin so the Presets tab
    shows WHOSE recommendation it is. Returns None when nothing translates
    (unknown units and no ratio — an honest absence, never a guess)."""
    label = entry.get("label") or "(unnamed)"
    origin = entry.get("origin") or "?"
    surface_speed = _convert(entry.get("vc"), _VC_FACTORS, VC_UNIT)
    chipload = _convert(entry.get("fz"), _FZ_FACTORS, FZ_UNIT)
    ratio_leaf = entry.get("ratio")
    ratio = ratio_leaf.get("value") if isinstance(ratio_leaf, dict) else None
    if surface_speed is None and chipload is None \
            and not isinstance(ratio, (int, float)):
        return None
    material = entry.get("material") or {}
    extras = entry.get("extras") or {}
    op = extras.get("freecad_op_type") \
        or OP_MAP_REVERSE.get(entry.get("op_type"))
    return {
        "name": "%s: %s" % (origin, label),
        "material_hint": {"uuid": material.get("uuid"),
                          "name": material.get("name")}
        if material.get("name") or material.get("uuid") else None,
        "op_type_hint": op,
        "surface_speed": surface_speed,
        "chipload": chipload,
        "vert_feed_ratio": ratio if isinstance(ratio, (int, float)) else 0.33,
        EXTERNAL_KEY: {"origin": origin, "label": label,
                       "id": entry.get("id"),
                       "source": entry.get("source")},
    }


def externalize(entries):
    """Server union entries -> deterministic, marked native presets.

    Non-freecad origins only (FreeCAD's own entries already live natively in
    the file), deduped by (origin, label), sorted so regeneration is stable
    and sync never sees churn without a real change."""
    seen, out = set(), []
    for entry in entries or []:
        if not isinstance(entry, dict) or entry.get("origin") == ORIGIN:
            continue
        key = (entry.get("origin"), entry.get("label"))
        if key in seen:
            continue
        seen.add(key)
        native = to_native(entry)
        if native is not None:
            out.append(native)
    out.sort(key=lambda p: (p[EXTERNAL_KEY]["origin"] or "",
                            p[EXTERNAL_KEY]["label"] or ""))
    return out


def split_native(presets):
    """Partition a doc's presets: (freecad's own, externally-materialized)."""
    own, external = [], []
    for preset in presets or []:
        if isinstance(preset, dict) and preset.get(EXTERNAL_KEY):
            external.append(preset)
        else:
            own.append(preset)
    return own, external


def translate(doc):
    """Translate a ``.fctb`` doc's presets into contribution bodies.

    Returns ``(contributions, skipped)`` where ``skipped`` is a list of
    ``(label, reason)`` for presets that cannot meet the server's floor —
    they stay in the client section, honestly un-promoted."""
    contributions, skipped = [], []
    presets = doc.get("presets")
    if not isinstance(presets, list):
        return contributions, skipped
    for preset in presets:
        if not isinstance(preset, dict):
            continue
        if preset.get(EXTERNAL_KEY):
            continue          # materialized from the union — never re-promoted
        label = derive_label(preset)
        hint = preset.get("material_hint")
        material_name = hint.get("name") if isinstance(hint, dict) else None
        if not material_name:
            skipped.append((label, "no material name — cannot normalize"))
            continue
        body = {"origin": ORIGIN, "label": label,
                "material": {"name": str(material_name)}}
        if isinstance(hint, dict) and hint.get("uuid"):
            body["material"]["uuid"] = str(hint["uuid"])
        vc, fz = preset.get("surface_speed"), preset.get("chipload")
        ratio = preset.get("vert_feed_ratio")
        if isinstance(vc, (int, float)) and vc > 0:
            body["vc"] = {"value": vc, "unit": VC_UNIT}
        if isinstance(fz, (int, float)) and fz > 0:
            body["fz"] = {"value": fz, "unit": FZ_UNIT}
        if isinstance(ratio, (int, float)) and ratio > 0:
            body["ratio"] = {"value": ratio}
        if "vc" not in body and "fz" not in body and "ratio" not in body:
            skipped.append((label, "no engineering values"))
            continue
        op = preset.get("op_type_hint")
        if op:
            body["extras"] = {"freecad_op_type": str(op)}
            mapped = OP_MAP.get(op)
            if mapped:
                body["op_type"] = mapped
        contributions.append(body)
    return contributions, skipped


def promote(client, record_id, doc, log=lambda msg: None):
    """Contribute a bit's presets (replace-own) and prune freecad-origin
    server entries whose preset no longer exists locally.

    Never raises for policy reasons: a server without the preset door (404)
    or a key without a needed scope (403) logs and moves on — sync itself
    must not fail over presets. Returns a summary dict."""
    contributions, skipped = translate(doc)
    summary = {"promoted": 0, "skipped": len(skipped), "pruned": 0,
               "blocked": 0}
    for label, reason in skipped:
        log("  preset '%s' not promoted: %s" % (label, reason))
    # A doc that never carried presets skips the whole dance (no extra GET
    # per tool). A doc whose presets key EXISTS — even emptied — still runs
    # the prune, so deleting your last preset in FreeCAD propagates.
    if "presets" not in doc:
        return summary

    for body in contributions:
        try:
            client.contribute_preset(record_id, body)
            summary["promoted"] += 1
        except Exception as exc:
            summary["blocked"] += 1
            log("  preset '%s' contribution failed: %s"
                % (body["label"], exc))

    # Prune: freecad-origin entries on the INSTANCE whose label vanished.
    labels = {body["label"] for body in contributions} \
        | {label for label, _ in skipped}
    try:
        entries = client.list_instance_presets(record_id)
    except Exception:
        return summary                    # old server / read trouble: done
    for entry in entries:
        if entry.get("origin") != ORIGIN or entry.get("scope") == "catalog":
            continue
        if entry.get("label") in labels:
            continue
        try:
            client.delete_instance_preset(record_id, entry["id"])
            summary["pruned"] += 1
            log("  preset '%s' removed (deleted in FreeCAD)"
                % entry.get("label"))
        except Exception as exc:
            summary["blocked"] += 1
            log("  stale preset '%s' left on server (%s) — remove it in "
                "the Web UI" % (entry.get("label"), exc))
    return summary
