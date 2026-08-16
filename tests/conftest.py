# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""Shared fixtures: the in-memory fake server and a populated tools dir."""
import json
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _set_path(canonical, path, field):
    """Fold a dotted ``path`` -> ``field`` into a nested canonical dict,
    exactly as loobric-server's assert door does."""
    node = canonical
    parts = path.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = field
    return canonical


class FakeServer:
    """In-memory stand-in for the sectioned tool-schema endpoints.

    Mirrors :class:`freecad.Loobric.client.LoobricApi`'s sync-lane methods and
    returns ``{internal, canonical, clients}`` records the way loobric-server's
    ``_response`` does: create/get/list/put-section/assert/members all return
    the full record; delete returns ``{"deleted": id}``. Canonical values are
    provenance-tagged Fields (``{value, source}``); a set's per-tool numbers are
    promoted into ``canonical.members`` as Fields (§7.4).
    """

    def __init__(self):
        self.instances = {}   # id -> sectioned record
        self.sets = {}        # id -> sectioned record
        self.catalogs = {}    # id -> sectioned ToolCatalogRecord
        self.setups = []      # active setup rows ({machine_id, tool_set_id})
        self.setup_views = {} # machine_id -> the derived setup view
        self.machines = {}    # id -> sectioned MachineRecord
        self.presets = {}     # (record_id, origin, label) -> entry dict
        self.catalog_groups = {}  # id -> Catalog entity (named collection)
        self._n = 0
        self._seed_catalog()

    # -- Catalogs: named collections of catalog records (server >= 0.14.0) ---

    def list_catalogs(self):
        return list(self.catalog_groups.values())

    def create_catalog(self, name, actor="human@cli"):
        cid = self._next("catgrp")
        row = self._blank(cid)
        row["canonical"] = {"name": self._field(name),
                            "members": self._field([])}
        self.catalog_groups[cid] = row
        return row

    def set_catalog_members(self, catalog_id, record_ids, actor="human@cli"):
        row = self.catalog_groups[catalog_id]
        row["canonical"]["members"] = self._field(list(dict.fromkeys(record_ids)))
        row["internal"]["version"] += 1
        return row

    # -- cutting data presets (the contribution door; server >= 0.13.0) ------

    def contribute_preset(self, record_id, body):
        key = (record_id, body["origin"], body["label"])
        entry = dict(body)
        entry.pop("actor", None)
        entry["id"] = self.presets[key]["id"] if key in self.presets \
            else self._next("preset")
        entry["source"] = "asserted:%s" % body.get("actor", "freecad")
        entry["scope"] = "instance"
        self.presets[key] = entry
        return self.instances[record_id]

    def list_instance_presets(self, record_id):
        return [dict(e) for (rid, _, _), e in sorted(self.presets.items())
                if rid == record_id]

    def delete_instance_preset(self, record_id, entry_id):
        for key, entry in list(self.presets.items()):
            if key[0] == record_id and entry["id"] == entry_id:
                del self.presets[key]
                return self.instances[record_id]
        raise KeyError(entry_id)

    # -- setups (the machine↔set relationship; read-only from the CAM side) --

    def active_setups(self):
        return list(self.setups)

    def setup_view(self, machine_id):
        return self.setup_views[machine_id]

    def list_machines(self):
        return list(self.machines.values())

    def make_setup(self, machine_id, tool_set_id, machine_name=None, notes=None):
        """Test driver: make `tool_set_id` the machine's active setup with a
        canned derived view carrying `notes` (the rows the set doesn't claim)."""
        self.setups.append({"id": self._next("map"), "machine_id": machine_id,
                            "tool_set_id": tool_set_id, "status": "active"})
        m = self._blank(machine_id)
        m["canonical"]["name"] = self._field(machine_name or machine_id)
        self.machines[machine_id] = m
        self.setup_views[machine_id] = {
            "machine_id": machine_id, "active": True,
            "tool_set_id": tool_set_id, "ready": not notes,
            "claims": [], "notes": notes or [],
            "attention": {"important": 0, "notes": len(notes or [])},
        }

    def _next(self, prefix):
        self._n += 1
        return "%s-%d" % (prefix, self._n)

    @staticmethod
    def _blank(rid):
        return {"internal": {"id": rid, "version": 1,
                             "created_at": "t0", "updated_at": "t0"},
                "canonical": {}, "clients": {}}

    @staticmethod
    def _field(value, source="asserted:human@web", unit=None):
        f = {"value": value, "source": source}
        if unit is not None:
            f["unit"] = unit
        return f

    @staticmethod
    def _section(client_version, client_item_id, data):
        return {"client_version": client_version or "0.2.0",
                "client_item_id": client_item_id,
                "created_at": "t0", "updated_at": "t0",
                "data": data or {}}

    # -- ToolInstanceRecords --------------------------------------------------

    def list_instances(self):
        return list(self.instances.values())

    def get_instance(self, record_id):
        return self.instances[record_id]

    def create_instance(self, data=None, client_item_id=None):
        rid = self._next("rec")
        rec = self._blank(rid)
        if data is not None or client_item_id is not None:
            rec["clients"]["freecad"] = self._section("0.2.0", client_item_id, data)
        self.instances[rid] = rec
        return rec

    def put_instance_section(self, record_id, data, client_item_id=None):
        rec = self.instances[record_id]
        rec["clients"]["freecad"] = self._section("0.2.0", client_item_id, data)
        rec["internal"]["version"] += 1
        return rec

    def assert_instance(self, record_id, path, value, actor="freecad"):
        rec = self.instances[record_id]
        _set_path(rec["canonical"], path,
                  {"value": value, "source": "asserted:%s" % actor})
        rec["internal"]["version"] += 1
        return rec

    def assert_instance_fields(self, record_id, asserts, actor="freecad"):
        return [self.assert_instance(record_id, p, v, actor) for p, v in asserts]

    def delete_instance(self, record_id):
        self.instances.pop(record_id, None)
        return {"deleted": record_id}

    # -- ToolCatalogRecords (M2: browse + create-instance) --------------------

    def _seed_catalog(self):
        """One catalog record to browse from: a manufacturer-identified type with
        nominal name/manufacturer/product_code and a diameter + shape."""
        cid = self._next("cat")
        self.catalogs[cid] = {
            "internal": {"id": cid, "version": 1,
                         "created_at": "t0", "updated_at": "t0"},
            "canonical": {
                "name": self._field('1/4" Downcut'),
                "manufacturer": self._field("Acme Tools"),
                "product_code": self._field("B201"),
                "geometry": {
                    "shape": self._field("endmill"),
                    "diameter": self._field(6.35, unit="mm"),
                    "flutes": self._field(2),
                },
            },
            "clients": {},
        }

    def list_catalog_records(self):
        return list(self.catalogs.values())

    def get_catalog_record(self, record_id):
        return self.catalogs[record_id]

    def create_instance_from_catalog(self, catalog_id, name=None):
        """Mint an UNBOUND instance from a catalog type. The catalog link is
        stamped canonical (asserted), the name copies the catalog's (or the
        override), and measured ``geometry`` is left EMPTY — the M2 contract: a
        just-created physical tool has not been measured, so its usable nominal
        geometry has to come from the catalog record, not the instance."""
        catalog = self.catalogs[catalog_id]
        rid = self._next("rec")
        rec = self._blank(rid)
        cat_name = (catalog["canonical"].get("name") or {}).get("value")
        _set_path(rec["canonical"], "catalog_type_id",
                  {"value": catalog_id, "source": "asserted:human@freecad"})
        _set_path(rec["canonical"], "name",
                  {"value": name if name is not None else cat_name,
                   "source": "asserted:human@freecad"})
        rec["canonical"]["geometry"] = {}        # measured geometry unknown
        self.instances[rid] = rec
        return rec

    # -- ToolSets -------------------------------------------------------------

    def list_sets(self):
        return list(self.sets.values())

    def get_set(self, record_id):
        return self.sets[record_id]

    def create_set(self, data=None, client_item_id=None):
        sid = self._next("set")
        rec = self._blank(sid)
        if data is not None or client_item_id is not None:
            rec["clients"]["freecad"] = self._section("0.2.0", client_item_id, data)
        self.sets[sid] = rec
        return rec

    def put_set_section(self, record_id, data, client_item_id=None):
        rec = self.sets[record_id]
        rec["clients"]["freecad"] = self._section("0.2.0", client_item_id, data)
        rec["internal"]["version"] += 1
        return rec

    def assert_set(self, record_id, path, value, actor="freecad"):
        rec = self.sets[record_id]
        _set_path(rec["canonical"], path,
                  {"value": value, "source": "asserted:%s" % actor})
        rec["internal"]["version"] += 1
        return rec

    def set_members(self, record_id, members, actor="freecad"):
        rec = self.sets[record_id]
        rec["canonical"]["members"] = [
            {"tool_record_id": m["tool_record_id"],
             "number": {"value": m.get("number"), "source": "asserted:%s" % actor}}
            for m in members]
        rec["internal"]["version"] += 1
        return rec

    def delete_set(self, record_id):
        self.sets.pop(record_id, None)
        return {"deleted": record_id}


@pytest.fixture
def tools_dir(tmp_path):
    """A FreeCAD-style Tools dir with 3 bits and a small library."""
    bit_dir = tmp_path / "Bit"
    lib_dir = tmp_path / "Library"
    bit_dir.mkdir()
    lib_dir.mkdir()
    for name in ["drill_5.0mm.fctb", "end_mill_6.0mm_2f.fctb", "probe.fctb"]:
        shutil.copy(FIXTURES / "bits" / name, bit_dir / name)
    (lib_dir / "default.fctl").write_text(json.dumps({
        "label": "default", "version": 1,
        "tools": [{"nr": 1, "path": "drill_5.0mm.fctb"},
                  {"nr": 2, "path": "end_mill_6.0mm_2f.fctb"}],
    }))
    return tmp_path
