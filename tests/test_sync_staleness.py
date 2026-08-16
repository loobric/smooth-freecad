# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""The 3/4"-endmill clobber loop (field finding 2026-08-16): classification
must judge each side against what THIS install last synced (the snapshot),
not the live server section — and a resurrected stale file (an old cached
doc rewritten over the mirror, old loobric.version and all) must never
auto-push over newer upstream truth."""
import json
from pathlib import Path

from conftest import FakeServer

from freecad.Loobric import sync


def read(p):
    return json.loads(Path(p).read_text())


def write(p, doc):
    Path(p).write_text(json.dumps(doc))


def push_everything(tools_dir, server):
    plan = sync.plan_sync(str(tools_dir), server)
    decisions = {i["key"]: "push" for i in plan["items"]}
    return sync.apply_sync(str(tools_dir), server, plan, decisions)


def bit_item(plan, basename):
    for item in plan["items"]:
        if item["kind"] == "bit" and item["basename"] == basename:
            return item
    raise KeyError(basename)


def endmill_rid(tools_dir):
    return read(tools_dir / "Bit" / "end_mill_6.0mm_2f.fctb")["loobric"]["record_id"]


class TestSnapshotBase:
    def test_sync_records_snapshot_and_version(self, tools_dir):
        server = FakeServer()
        push_everything(tools_dir, server)
        state = sync._load_sync_state(str(tools_dir))
        rid = endmill_rid(tools_dir)
        assert rid in state["bit_snapshots"]
        assert state["bit_versions"][rid] == \
            read(tools_dir / "Bit" / "end_mill_6.0mm_2f.fctb")["loobric"]["version"]

    def test_both_sides_changed_is_conflict_not_push(self, tools_dir):
        # Server canonical corrected AND the local file changed since the
        # last sync. The pre-0.7.0 live-section base made this a confident
        # "push" (the local side matched nothing, the server side matched
        # its own section) — which is exactly the clobber.
        server = FakeServer()
        push_everything(tools_dir, server)
        rid = endmill_rid(tools_dir)
        server.assert_instance(rid, "geometry.diameter", 19.05)
        path = tools_dir / "Bit" / "end_mill_6.0mm_2f.fctb"
        doc = read(path)
        doc["parameter"]["CuttingEdgeHeight"] = "99.00 mm"
        write(path, doc)
        plan = sync.plan_sync(str(tools_dir), server)
        assert bit_item(plan, "end_mill_6.0mm_2f.fctb")["action"] == "conflict"

    def test_server_only_change_is_pull(self, tools_dir):
        server = FakeServer()
        push_everything(tools_dir, server)
        server.assert_instance(endmill_rid(tools_dir),
                               "geometry.diameter", 19.05)
        plan = sync.plan_sync(str(tools_dir), server)
        assert bit_item(plan, "end_mill_6.0mm_2f.fctb")["action"] == "pull"

    def test_local_only_change_is_push(self, tools_dir):
        server = FakeServer()
        push_everything(tools_dir, server)
        path = tools_dir / "Bit" / "end_mill_6.0mm_2f.fctb"
        doc = read(path)
        doc["parameter"]["CuttingEdgeHeight"] = "99.00 mm"
        write(path, doc)
        plan = sync.plan_sync(str(tools_dir), server)
        assert bit_item(plan, "end_mill_6.0mm_2f.fctb")["action"] == "push"

    def test_pull_converges(self, tools_dir):
        # After taking the server side of a conflict, the next plan must be
        # 'unchanged' — the loop the field report described ("I force server
        # download and it immediately repeats") must not exist.
        server = FakeServer()
        push_everything(tools_dir, server)
        rid = endmill_rid(tools_dir)
        server.assert_instance(rid, "geometry.diameter", 19.05)
        plan = sync.plan_sync(str(tools_dir), server)
        item = bit_item(plan, "end_mill_6.0mm_2f.fctb")
        sync.apply_sync(str(tools_dir), server, plan, {item["key"]: "pull"})
        plan = sync.plan_sync(str(tools_dir), server)
        assert bit_item(plan, "end_mill_6.0mm_2f.fctb")["action"] == "unchanged"


class TestStaleFileGuard:
    def test_resurrected_stale_file_is_conflict(self, tools_dir):
        # The phantom-writer scenario: after a good sync at version N, an
        # old cached copy of the doc (old content AND old loobric.version)
        # is rewritten over the mirror file. Content-wise it looks like a
        # local edit; the version stamp proves it predates what this
        # install already synced — never auto-push it.
        server = FakeServer()
        push_everything(tools_dir, server)
        path = tools_dir / "Bit" / "end_mill_6.0mm_2f.fctb"
        stale = read(path)                      # snapshot the good state
        stale["parameter"]["ShankDiameter"] = "3.00 mm"
        stale["loobric"]["version"] = max(
            1, stale["loobric"]["version"] - 5)  # an OLD stamp
        # meanwhile this install syncs again at a newer version
        doc = read(path)
        doc["parameter"]["CuttingEdgeHeight"] = "40.00 mm"
        write(path, doc)
        push_everything(tools_dir, server)
        write(path, stale)                      # the phantom rewrite
        plan = sync.plan_sync(str(tools_dir), server)
        item = bit_item(plan, "end_mill_6.0mm_2f.fctb")
        assert item["action"] == "conflict"
        assert "stale copy" in item["detail"]

    def test_fresh_edit_on_current_version_still_pushes(self, tools_dir):
        # A genuine local edit carries the CURRENT version stamp — the
        # guard must not get in its way, even when unrelated server-side
        # version bumps happened (usage hours, labels, presets…).
        server = FakeServer()
        push_everything(tools_dir, server)
        rid = endmill_rid(tools_dir)
        path = tools_dir / "Bit" / "end_mill_6.0mm_2f.fctb"
        doc = read(path)
        doc["parameter"]["CuttingEdgeHeight"] = "40.00 mm"
        write(path, doc)
        plan = sync.plan_sync(str(tools_dir), server)
        assert bit_item(plan, "end_mill_6.0mm_2f.fctb")["action"] == "push"

    def test_legacy_state_without_snapshot_still_classifies(self, tools_dir):
        # Records synced before bit_snapshots existed fall back to the old
        # live-section base — never a crash, and server-only changes still
        # classify as pull.
        server = FakeServer()
        push_everything(tools_dir, server)
        state = sync._load_sync_state(str(tools_dir))
        state["bit_snapshots"] = {}
        state["bit_versions"] = {}
        sync._save_sync_state(str(tools_dir), state)
        server.assert_instance(endmill_rid(tools_dir),
                               "geometry.diameter", 19.05)
        plan = sync.plan_sync(str(tools_dir), server)
        assert bit_item(plan, "end_mill_6.0mm_2f.fctb")["action"] == "pull"


class TestApplyDrift:
    def test_deleted_server_pull_tolerates_missing_file(self, tools_dir):
        # The field crash 2026-08-16: the file vanished between plan and
        # apply (stale plan window / second writer). "Delete the local
        # file" on an already-deleted file is success, and one item's
        # filesystem drift must never abort the batch.
        server = FakeServer()
        push_everything(tools_dir, server)
        rid = endmill_rid(tools_dir)
        server.delete_instance(rid)
        plan = sync.plan_sync(str(tools_dir), server)
        item = bit_item(plan, "end_mill_6.0mm_2f.fctb")
        assert item["action"] == "deleted_server"
        (tools_dir / "Bit" / "end_mill_6.0mm_2f.fctb").unlink()  # the drift
        summary = sync.apply_sync(str(tools_dir), server, plan,
                                  {item["key"]: "pull"})
        assert summary["deleted"] == 1
        assert summary["errors"] == []
        state = sync._load_sync_state(str(tools_dir))
        assert rid not in state["records"]
