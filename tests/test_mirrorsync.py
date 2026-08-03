# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for the asset-store mirror policy (mirrorsync).

The 0.6.0 contract: a refresh applies every UNAMBIGUOUS direction —
server-only pulls, local-only pushes, single-side changes follow the changed
side, and deletions PROPAGATE both ways (with a trash copy + retention on
the mirror side). Only a conflict is held for the Sync window. A read-only
key applies pull directions only and reports the push-shaped remainder.

The mirror is an ordinary tools dir, so these tests drive refresh_mirror
against the sectioned FakeServer exactly like the plan/apply suite.
"""
import json
from pathlib import Path

from freecad.Loobric import mirrorsync
from conftest import FakeServer

FIXTURES = Path(__file__).parent / "fixtures"


def read(p):
    return json.loads(Path(p).read_text())


def write(p, doc):
    Path(p).write_text(json.dumps(doc, indent=2))


def mirror(tmp_path):
    """An empty mirror tools dir (what assetstore.mirror_dir() creates)."""
    root = tmp_path / "mirror"
    (root / "Bit").mkdir(parents=True)
    (root / "Library").mkdir(parents=True)
    return root


def seed_server_bit(server, basename="drill_5.0mm.fctb"):
    """A tool that exists only on the server, with a FreeCAD section so the
    .fctb can be regenerated verbatim."""
    doc = read(FIXTURES / "bits" / basename)
    record = server.create_instance(data={"fctb": doc},
                                    client_item_id=basename)
    rid = record["internal"]["id"]
    server.assert_instance(rid, "name", doc.get("name") or basename)
    server.assert_instance(rid, "geometry.shape", "drill")
    return record


def seed_server_library(server, member_record, name="server-lib", nr=7):
    record = server.create_set(data={"fctl_label": name, "version": 1},
                               client_item_id=name)
    sid = record["internal"]["id"]
    server.assert_set(sid, "name", name)
    server.set_members(sid, [{
        "tool_record_id": member_record["internal"]["id"], "number": nr}])
    return server.get_set(sid)


# -- the unambiguous directions ------------------------------------------------


def test_initial_fill_pulls_server_content(tmp_path):
    """An empty mirror materializes the server's bits and libraries."""
    server = FakeServer()
    bit = seed_server_bit(server)
    seed_server_library(server, bit)
    root = mirror(tmp_path)

    summary = mirrorsync.refresh_mirror(root, server)

    assert summary["pulled"] == 2 and not summary["errors"]
    assert not summary["held"]
    bit_files = list((root / "Bit").glob("*.fctb"))
    lib_files = list((root / "Library").glob("*.fctl"))
    assert len(bit_files) == 1 and len(lib_files) == 1
    fctl = read(lib_files[0])
    assert fctl["label"] == "server-lib"
    assert fctl["tools"][0]["nr"] == 7
    assert fctl["tools"][0]["path"] == bit_files[0].name


def test_local_creation_pushes(tmp_path):
    """A bit written into the mirror (a tool created through the store)
    uploads and gets its identity written back."""
    server = FakeServer()
    root = mirror(tmp_path)
    doc = read(FIXTURES / "bits" / "end_mill_6.0mm_2f.fctb")
    path = root / "Bit" / "end_mill_6.0mm_2f.fctb"
    write(path, doc)

    summary = mirrorsync.refresh_mirror(root, server)

    assert summary["pushed"] == 1 and not summary["errors"]
    assert len(server.instances) == 1
    assert read(path)["loobric"]["record_id"] in server.instances


def test_server_change_pulls_over_local(tmp_path):
    """Changed only on the server: the mirror follows without asking."""
    server = FakeServer()
    root = mirror(tmp_path)
    bit = seed_server_bit(server)
    mirrorsync.refresh_mirror(root, server)

    server.assert_instance(bit["internal"]["id"], "name", "Renamed on server")
    summary = mirrorsync.refresh_mirror(root, server)

    assert summary["pulled"] >= 1 and not summary["held"]
    path = next((root / "Bit").glob("*.fctb"))
    assert read(path)["name"] == "Renamed on server"


def test_local_change_pushes(tmp_path):
    """Changed only in the mirror (edited in a FreeCAD editor): uploads."""
    server = FakeServer()
    root = mirror(tmp_path)
    seed_server_bit(server)
    mirrorsync.refresh_mirror(root, server)

    path = next((root / "Bit").glob("*.fctb"))
    doc = read(path)
    doc["name"] = "Renamed locally"
    write(path, doc)
    summary = mirrorsync.refresh_mirror(root, server)

    assert summary["pushed"] == 1 and not summary["held"]
    record = next(iter(server.instances.values()))
    assert record["canonical"]["name"]["value"] == "Renamed locally"


# -- deletions propagate, with the trash as the net -----------------------------


def test_local_deletion_propagates_to_server(tmp_path):
    """A file deleted from the mirror (the store trashes it at delete time)
    deletes the server record on the next refresh."""
    server = FakeServer()
    root = mirror(tmp_path)
    seed_server_bit(server)
    mirrorsync.refresh_mirror(root, server)

    next((root / "Bit").glob("*.fctb")).unlink()
    summary = mirrorsync.refresh_mirror(root, server)

    assert summary["deleted"] == 1 and not summary["held"]
    assert len(server.instances) == 0


def test_server_deletion_removes_local_via_trash(tmp_path):
    """A record deleted on the server removes the mirror file — but a copy
    lands in .trash first."""
    server = FakeServer()
    root = mirror(tmp_path)
    bit = seed_server_bit(server)
    mirrorsync.refresh_mirror(root, server)

    server.delete_instance(bit["internal"]["id"])
    summary = mirrorsync.refresh_mirror(root, server)

    assert summary["deleted"] == 1 and not summary["held"]
    assert not list((root / "Bit").glob("*.fctb"))
    trashed = list((root / mirrorsync.TRASH_DIR).rglob("*.fctb"))
    assert len(trashed) == 1
    assert read(trashed[0]).get("loobric")          # the full file, recoverable


def test_trash_prunes_by_retention(tmp_path):
    root = mirror(tmp_path)
    victim = root / "Bit" / "old.fctb"
    write(victim, {"name": "old"})
    old_batch = mirrorsync.trash_file(root, victim, now=1_000_000)
    write(victim, {"name": "new"})
    new_batch = mirrorsync.trash_file(
        root, victim, now=1_000_000 + 40 * 86400)

    removed = mirrorsync.prune_trash(root, retention_days=30,
                                     now=1_000_000 + 41 * 86400)

    assert removed == 1
    assert not Path(old_batch).exists()
    assert Path(new_batch).exists()


# -- read-only keys: pull-only, pushes reported ---------------------------------


def test_read_only_refresh_pulls_but_never_pushes(tmp_path):
    server = FakeServer()
    root = mirror(tmp_path)
    seed_server_bit(server)                          # pull direction: allowed
    stray = read(FIXTURES / "bits" / "end_mill_6.0mm_2f.fctb")
    write(root / "Bit" / "end_mill_6.0mm_2f.fctb", stray)   # push direction

    summary = mirrorsync.refresh_mirror(root, server, read_only=True)

    assert summary["pulled"] == 1 and summary["pushed"] == 0
    assert len(server.instances) == 1               # the stray never uploaded
    assert [i["name"] for i in summary["ro_blocked"]]


# -- per-server mirrors ----------------------------------------------------------


def test_server_slug_separates_servers(tmp_path):
    a = mirrorsync.server_slug("https://api.loobric.com")
    b = mirrorsync.server_slug("https://shop.example:8080")
    assert a != b
    assert a.startswith("api.loobric.com-")
    assert "/" not in a + b and ":" not in a + b
    # same server, same slug — the mirror survives restarts
    assert a == mirrorsync.server_slug("https://api.loobric.com/")
    root = mirrorsync.mirror_root(tmp_path, "https://api.loobric.com")
    assert (Path(root) / "Bit").is_dir() and (Path(root) / "Library").is_dir()


# -- missing custom shapes are a warning, not a mystery ---------------------------


def test_missing_shapes_reports_unresolvable_references(tmp_path):
    root = mirror(tmp_path)
    doc = read(FIXTURES / "bits" / "end_mill_6.0mm_2f.fctb")
    doc["shape"] = "myweirdform.fcstd"
    write(root / "Bit" / "custom.fctb", doc)
    ok = read(FIXTURES / "bits" / "drill_5.0mm.fctb")
    write(root / "Bit" / "stock.fctb", ok)

    missing = mirrorsync.missing_shapes(
        root, available={ok.get("shape") or "drill.fcstd"})

    assert missing == [("myweirdform.fcstd", ["custom.fctb"])]


# -- what a refresh must NOT do unattended -------------------------------------


def test_conflict_is_held_untouched(tmp_path):
    """Changed on both sides: neither side is overwritten; the item is
    reported for the Sync window."""
    server = FakeServer()
    root = mirror(tmp_path)
    bit = seed_server_bit(server)
    mirrorsync.refresh_mirror(root, server)

    path = next((root / "Bit").glob("*.fctb"))
    doc = read(path)
    doc["name"] = "Local rename"
    write(path, doc)
    server.assert_instance(bit["internal"]["id"], "name", "Server rename")

    summary = mirrorsync.refresh_mirror(root, server)

    held = {i["action"] for i in summary["held"]}
    assert held == {"conflict"}
    assert read(path)["name"] == "Local rename"                 # untouched
    record = server.get_instance(bit["internal"]["id"])
    assert record["canonical"]["name"]["value"] == "Server rename"
    assert mirrorsync.describe_held(summary["held"])            # reportable




# -- folding the 0.6.0 Tools/ mapping defect back into the mirror --------------


def tools_tree(root):
    """A stray Tools/ subtree as the 0.6.0 store mapping produced it."""
    (root / "Tools" / "Bit").mkdir(parents=True)
    (root / "Tools" / "Library").mkdir(parents=True)
    return root / "Tools"


def test_migrate_absent_tree_is_noop(tmp_path):
    root = mirror(tmp_path)

    assert mirrorsync.migrate_tools_subtree(root) == 0
    assert not (root / "Tools").exists()


def test_migrate_moves_stranded_files(tmp_path):
    """A write that landed under Tools/ (never pushed) moves into the real
    tree, and the emptied Tools/ subtree disappears."""
    root = mirror(tmp_path)
    tools = tools_tree(root)
    doc = read(FIXTURES / "bits" / "drill_5.0mm.fctb")
    write(tools / "Bit" / "stranded.fctb", doc)
    write(tools / "Library" / "Default.fctl", {"tools": []})

    logs = []
    moved = mirrorsync.migrate_tools_subtree(root, log=logs.append)

    assert moved == 2
    assert read(root / "Bit" / "stranded.fctb") == doc
    assert (root / "Library" / "Default.fctl").exists()
    assert not (root / "Tools").exists()
    assert any("stranded.fctb" in m for m in logs)


def test_migrate_drops_identical_duplicates(tmp_path):
    """The stock seeds 0.6.0 wrote under Tools/ vanish when the synced tree
    already has the same content — no move, no trash."""
    root = mirror(tmp_path)
    tools = tools_tree(root)
    doc = read(FIXTURES / "bits" / "drill_5.0mm.fctb")
    write(root / "Bit" / "drill.fctb", doc)
    write(tools / "Bit" / "drill.fctb", doc)

    moved = mirrorsync.migrate_tools_subtree(root)

    assert moved == 0
    assert read(root / "Bit" / "drill.fctb") == doc
    assert not (root / "Tools").exists()
    assert not (root / mirrorsync.TRASH_DIR).exists()


def test_migrate_conflict_keeps_synced_copy_and_trashes(tmp_path):
    """Same name, different content: the server-synced copy stays; the
    Tools/ copy is recoverable from the trash, not silently gone."""
    root = mirror(tmp_path)
    tools = tools_tree(root)
    synced = read(FIXTURES / "bits" / "drill_5.0mm.fctb")
    write(root / "Bit" / "drill.fctb", synced)
    divergent = dict(synced, name="Edited through the 0.6.0 store")
    write(tools / "Bit" / "drill.fctb", divergent)

    logs = []
    moved = mirrorsync.migrate_tools_subtree(root, log=logs.append, now=1000)

    assert moved == 0
    assert read(root / "Bit" / "drill.fctb") == synced          # untouched
    trashed = list((root / mirrorsync.TRASH_DIR).rglob("drill.fctb"))
    assert len(trashed) == 1
    assert read(trashed[0]) == divergent
    assert any("trash" in m for m in logs)
    assert not (root / "Tools").exists()


def test_migrate_leaves_unknown_files(tmp_path):
    """Anything under Tools/ the migration doesn't recognize stays put and
    keeps the dir alive."""
    root = mirror(tmp_path)
    tools = tools_tree(root)
    (tools / "notes.txt").write_text("not ours to judge")
    doc = read(FIXTURES / "bits" / "drill_5.0mm.fctb")
    write(tools / "Bit" / "stranded.fctb", doc)

    moved = mirrorsync.migrate_tools_subtree(root)

    assert moved == 1
    assert (tools / "notes.txt").exists()
    assert not (tools / "Bit").exists()


def test_mirror_mapping_matches_reconciled_layout(tmp_path):
    """The store's URI mapping and the dirs mirror_root() creates are the
    same layout — the 0.6.0 defect was exactly these disagreeing."""
    root = Path(mirrorsync.mirror_root(tmp_path, "https://api.example.com"))

    for pattern in mirrorsync.MIRROR_MAPPING.values():
        assert (root / pattern.split("/", 1)[0]).is_dir()
    assert mirrorsync.MIRROR_MAPPING["toolbit"].endswith(".fctb")
    assert mirrorsync.MIRROR_MAPPING["toolbitlibrary"].endswith(".fctl")
