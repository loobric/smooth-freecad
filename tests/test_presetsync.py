# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""Preset promotion (presetsync.py): translating FreeCAD's native F&S
presets into the server's ratified normal form and walking them through
the contribution door — replace-own, floor-honest, prune-on-delete."""
import pytest
from conftest import FakeServer

from freecad.Loobric import presetsync


def _preset(**overrides):
    preset = {"name": "alu finish", "material_hint": {"uuid": "u-1",
                                                      "name": "6061-T6"},
              "op_type_hint": "profile", "surface_speed": 250.0,
              "chipload": 0.05, "vert_feed_ratio": 0.33}
    preset.update(overrides)
    return preset


def _doc(*presets):
    return {"name": "6mm endmill", "shape": "endmill.fcstd",
            "presets": list(presets)}


# -- translate ---------------------------------------------------------------

class TestTranslate:
    def test_full_preset(self):
        (body,), skipped = presetsync.translate(_doc(_preset()))
        assert skipped == []
        assert body["origin"] == "freecad"
        assert body["label"] == "alu finish"
        assert body["material"] == {"name": "6061-T6", "uuid": "u-1"}
        assert body["op_type"] == "profiling"
        assert body["vc"] == {"value": 250.0, "unit": "m/min"}
        assert body["fz"] == {"value": 0.05, "unit": "mm"}
        assert body["ratio"] == {"value": 0.33}
        assert body["extras"] == {"freecad_op_type": "profile"}

    def test_label_falls_back_to_material_op_summary(self):
        (body,), _ = presetsync.translate(_doc(_preset(name=None)))
        assert body["label"] == "6061-T6 / profile"

    def test_op_vocabulary_mapping(self):
        for freecad_op, loobric_op in (("profile", "profiling"),
                                       ("pocket", "pocketing"),
                                       ("slot", "slotting"),
                                       ("drill", "drilling"),
                                       ("adaptive", "adaptive")):
            (body,), _ = presetsync.translate(
                _doc(_preset(op_type_hint=freecad_op)))
            assert body["op_type"] == loobric_op

    def test_unmappable_op_stays_verbatim_in_extras_only(self):
        (body,), _ = presetsync.translate(
            _doc(_preset(op_type_hint="surface_finish")))
        assert "op_type" not in body
        assert body["extras"] == {"freecad_op_type": "surface_finish"}

    def test_no_material_name_is_skipped_not_guessed(self):
        contributions, skipped = presetsync.translate(
            _doc(_preset(material_hint=None)))
        assert contributions == []
        assert skipped[0][1].startswith("no material name")
        # UUID-only hints can't meet the floor either (server needs a name).
        contributions, skipped = presetsync.translate(
            _doc(_preset(material_hint={"uuid": "u-2", "name": None})))
        assert contributions == []

    def test_no_engineering_values_is_skipped(self):
        contributions, skipped = presetsync.translate(_doc(_preset(
            surface_speed=None, chipload=None, vert_feed_ratio=0)))
        assert contributions == []
        assert skipped[0][1] == "no engineering values"

    def test_doc_without_presets(self):
        assert presetsync.translate({"name": "plain"}) == ([], [])


# -- promote -----------------------------------------------------------------

class TestPromote:
    def _record(self, fake):
        rec = fake.create_instance({"fctb": {}}, "t.fctb")
        return rec["internal"]["id"]

    def test_promotes_and_is_idempotent(self, fake_client):
        rid = self._record(fake_client)
        doc = _doc(_preset(), _preset(name="alu rough", chipload=0.09))
        s1 = presetsync.promote(fake_client, rid, doc)
        s2 = presetsync.promote(fake_client, rid, doc)
        assert (s1["promoted"], s2["promoted"]) == (2, 2)
        assert len(fake_client.list_instance_presets(rid)) == 2   # replace-own

    def test_prunes_entry_deleted_in_freecad(self, fake_client):
        rid = self._record(fake_client)
        presetsync.promote(fake_client, rid,
                           _doc(_preset(), _preset(name="alu rough")))
        summary = presetsync.promote(fake_client, rid, _doc(_preset()))
        assert summary["pruned"] == 1
        labels = [e["label"] for e in fake_client.list_instance_presets(rid)]
        assert labels == ["alu finish"]

    def test_emptied_presets_list_prunes_everything(self, fake_client):
        rid = self._record(fake_client)
        presetsync.promote(fake_client, rid, _doc(_preset()))
        summary = presetsync.promote(fake_client, rid, _doc())
        assert summary["pruned"] == 1
        assert fake_client.list_instance_presets(rid) == []

    def test_doc_that_never_had_presets_makes_no_calls(self, fake_client):
        rid = self._record(fake_client)
        summary = presetsync.promote(fake_client, rid, {"name": "plain"})
        assert summary == {"promoted": 0, "skipped": 0, "pruned": 0,
                           "blocked": 0}

    def test_skipped_preset_is_not_pruned(self, fake_client):
        # A preset that loses its material hint stops being promotable, but
        # its existing server entry must NOT be pruned as "deleted" — the
        # local preset still exists.
        rid = self._record(fake_client)
        presetsync.promote(fake_client, rid, _doc(_preset()))
        summary = presetsync.promote(
            fake_client, rid, _doc(_preset(material_hint=None)))
        assert summary["pruned"] == 0
        assert len(fake_client.list_instance_presets(rid)) == 1

    def test_contribution_failure_blocks_not_raises(self, fake_client):
        rid = self._record(fake_client)
        def boom(record_id, body):
            raise RuntimeError("403 delete scope")
        fake_client.contribute_preset = boom
        summary = presetsync.promote(fake_client, rid, _doc(_preset()))
        assert summary["blocked"] == 1

    def test_prune_failure_logs_not_raises(self, fake_client):
        rid = self._record(fake_client)
        presetsync.promote(fake_client, rid, _doc(_preset()))
        def denied(record_id, entry_id):
            raise RuntimeError("403")
        fake_client.delete_instance_preset = denied
        lines = []
        summary = presetsync.promote(fake_client, rid, _doc(),
                                     log=lines.append)
        assert summary["blocked"] == 1
        assert any("Web UI" in line for line in lines)

    def test_catalog_scope_entries_are_never_pruned(self, fake_client):
        rid = self._record(fake_client)
        # A manufacturer entry inherited from the linked catalog rides the
        # union scope-marked; FreeCAD must not treat it as its own.
        key = (rid, "manufacturer", "chart row")
        fake_client.presets[key] = {"id": "cat-1", "origin": "manufacturer",
                                    "label": "chart row", "scope": "catalog"}
        summary = presetsync.promote(fake_client, rid, _doc(_preset()))
        assert summary["pruned"] == 0
        assert any(e["origin"] == "manufacturer"
                   for e in fake_client.list_instance_presets(rid))


# -- pull side: materializing external union entries -------------------------

def _entry(**overrides):
    entry = {"id": "e-1", "origin": "manufacturer", "label": "6061 profiling",
             "material": {"name": "6061-T6", "uuid": "u-9"},
             "op_type": "profiling",
             "vc": {"value": 250, "unit": "m/min"},
             "fz": {"value": 0.05, "unit": "mm"},
             "ratio": {"value": 0.4},
             "source": "asserted:human@web"}
    entry.update(overrides)
    return {k: v for k, v in entry.items() if v is not None}


class TestToNative:
    def test_full_entry(self):
        native = presetsync.to_native(_entry())
        assert native["name"] == "manufacturer: 6061 profiling"
        assert native["material_hint"] == {"uuid": "u-9", "name": "6061-T6"}
        assert native["op_type_hint"] == "profile"
        assert native["surface_speed"] == 250
        assert native["chipload"] == 0.05
        assert native["vert_feed_ratio"] == 0.4
        marker = native[presetsync.EXTERNAL_KEY]
        assert marker["origin"] == "manufacturer"
        assert marker["source"] == "asserted:human@web"

    def test_imperial_units_convert_to_native_fixed_units(self):
        native = presetsync.to_native(_entry(
            vc={"value": 800, "unit": "sfm"},
            fz={"value": 0.002, "unit": "in"}))
        assert native["surface_speed"] == pytest.approx(243.84)
        assert native["chipload"] == pytest.approx(0.0508)

    def test_unknown_unit_is_dropped_never_guessed(self):
        native = presetsync.to_native(_entry(vc={"value": 9, "unit": "furlongs"}))
        assert native["surface_speed"] is None
        assert native["chipload"] == 0.05     # the rest still translates

    def test_nothing_translatable_returns_none(self):
        assert presetsync.to_native(_entry(
            vc={"value": 9, "unit": "furlongs"}, fz=None, ratio=None)) is None

    def test_verbatim_freecad_op_in_extras_wins(self):
        native = presetsync.to_native(_entry(
            op_type=None, extras={"freecad_op_type": "surface_finish"}))
        assert native["op_type_hint"] == "surface_finish"

    def test_unmappable_server_op_is_absent(self):
        assert presetsync.to_native(_entry(op_type="facing"))["op_type_hint"] is None


class TestExternalize:
    def test_freecad_origin_is_excluded(self):
        out = presetsync.externalize([_entry(), _entry(origin="freecad",
                                                       label="mine")])
        assert [p[presetsync.EXTERNAL_KEY]["origin"] for p in out] \
            == ["manufacturer"]

    def test_deterministic_order_and_dedupe(self):
        entries = [_entry(origin="user", label="b"),
                   _entry(origin="manufacturer", label="a"),
                   _entry(origin="user", label="b", vc={"value": 1,
                                                        "unit": "m/min"})]
        out = presetsync.externalize(entries)
        keys = [(p[presetsync.EXTERNAL_KEY]["origin"],
                 p[presetsync.EXTERNAL_KEY]["label"]) for p in out]
        assert keys == [("manufacturer", "a"), ("user", "b")]

    def test_marked_presets_are_never_promoted(self):
        doc = _doc(presetsync.to_native(_entry()), _preset())
        contributions, skipped = presetsync.translate(doc)
        assert [b["label"] for b in contributions] == ["alu finish"]
        assert skipped == []


class TestRegeneration:
    """mapping.instance_to_fctb materializes the union (pull side)."""

    def _record(self, own_presets=None, canonical_entries=None):
        record = {"internal": {"id": "rec-1", "version": 3},
                  "canonical": {"name": {"value": "t", "source": "s"}},
                  "clients": {"freecad": {"data": {"fctb": {
                      "version": 2, "name": "t", "shape": "endmill.fcstd",
                      "shape-type": "Endmill", "parameter": {},
                      **({"presets": own_presets}
                         if own_presets is not None else {})}}}}}
        if canonical_entries is not None:
            record["canonical"]["presets"] = {
                "value": canonical_entries, "source": "derived:preset-union"}
        return record

    def test_external_entries_materialize(self):
        from freecad.Loobric import mapping
        doc = mapping.instance_to_fctb(self._record(
            own_presets=[_preset()], canonical_entries=[_entry()]))
        own, external = presetsync.split_native(doc["presets"])
        assert [p["name"] for p in own] == ["alu finish"]
        assert [p["name"] for p in external] == ["manufacturer: 6061 profiling"]

    def test_catalog_presets_ride_in(self):
        from freecad.Loobric import mapping
        doc = mapping.instance_to_fctb(
            self._record(), catalog_presets=[_entry(label="chart row")])
        _, external = presetsync.split_native(doc.get("presets") or [])
        assert [p["name"] for p in external] == ["manufacturer: chart row"]

    def test_stale_external_copies_refresh_from_union(self):
        # The file carries a materialized copy; the union changed (entry
        # gone). Regeneration drops the stale copy — a preset deleted
        # elsewhere disappears here.
        from freecad.Loobric import mapping
        stale = presetsync.to_native(_entry(label="gone"))
        doc = mapping.instance_to_fctb(self._record(
            own_presets=[stale], canonical_entries=[]))
        assert doc["presets"] == []

    def test_regeneration_is_stable(self):
        from freecad.Loobric import mapping
        record = self._record(own_presets=[_preset()],
                              canonical_entries=[_entry()])
        assert mapping.instance_to_fctb(record) \
            == mapping.instance_to_fctb(record)

    def test_no_presets_anywhere_adds_no_key(self):
        from freecad.Loobric import mapping
        assert "presets" not in mapping.instance_to_fctb(self._record())


@pytest.fixture
def fake_client():
    return FakeServer()
