# Changelog

All notable changes to **loobric-freecad** (the FreeCAD CAM client for Loobric) are
recorded here. This project adheres to [Semantic Versioning](https://semver.org/).

## [0.4.0] — 2026-07-29 (pairs with loobric-server 0.7.0 setups, MAPPING_PLAN)

### Changed (BREAKING with servers < 0.7.0 for set↔machine linking only)
- **Library numbers are durable claims.** A pulled `.fctl` `nr` is the
  member's asserted claim; the machine's observed number is adopted ONLY when
  no claim exists (the mounted-before-claimed case). A mismount (CAM says
  T14, machine has T9) keeps T14 in the library — conceding is the
  programmer's explicit edit, never the sync's. Ends the round-trip that
  laundered claims into observations.
- **"Link to machine…" → "Setup status…" (read-only).** Which machine runs
  which set is the operator's act (`loobric use-set`); the context menu now
  shows where the set is active and how reality compares (READY / unmet
  claims / notes), or "not active on any machine — every number is
  provisional". `link_set_machine` removed from the embedded client; setups
  methods (`list_setups` / `reconciliation`) added.
- The sync view's library rows carry a setup note — "not active on any
  machine - numbers provisional", "setup: all claims met", or the unmet-claim
  counts — so provisional numbers are visible where the programmer syncs.

### Added
- **Machine notes in the sync view.** Rows the active setup doesn't claim —
  "T8 — unknown tool", the permanently mounted probe — have no `.fctb`, so
  FreeCAD's own editors structurally cannot show them; they now appear as
  display-only rows under the owning library in the sync plan (action `note`:
  informational band, never counted as an exception, never applied, excluded
  from rollups — MAPPING_PLAN §5.3's "information, not a task", in the tree).
  Best-effort: a pre-setups server contributes nothing.

## [0.3.2] — 2026-06-29

### Added
- **`loobric list-users` — the admin account roster.** A reference-client verb
  (and `Client.list_users()` library method) onto loobric-server's new
  `GET /api/v1/admin/users`: how many accounts exist and who they are (email,
  role, flags, API-key count, created date), newest first. Admin-only on the
  server; an older server with no such endpoint reports it plainly. No secrets
  are shown — never a password hash or key material. Needs loobric-server ≥ 0.3.5.

## [0.3.1] — 2026-06-23

### Fixed
- A plain download no longer triggers a phantom "local is newer — upload"
  prompt that forced a second apply. The client now records its section baseline
  on pull, so a download converges in a single apply (download is symmetric with
  upload).

## [0.3.0] — 2026-06-21

The **M2** client: browse the server's catalog records and create tools from
them. Tracks loobric-server 0.2.0.

### Added
- **Catalog tab** — browse `ToolCatalogRecord`s (name, manufacturer, product
  code, geometry, provenance source); read-only, no authoring.
- **Create tool from catalog** — makes an **unbound** server instance from the
  catalog type and immediately materializes a local `.fctb` in the active tools
  library, pre-filled from the catalog's nominal geometry and linked to the new
  instance. It lands **synced** (FreeCAD's client section is written back as the
  sync base); the instance's canonical geometry stays empty by design (the
  nominal geometry is reachable through the catalog link). Reload the CAM tool
  library to see the new tool in the editor.

### Changed
- **Re-vendored `loobric.py`** from loobric-server 0.2.0 (the M2 reference client):
  `create_instance_from_catalog`, `add_to_set` / `remove_from_set`,
  `show-tool-set`, `server_version`. Backwards-compatible — every existing call
  site is unchanged.

## [0.2.1] — 2026-06-20

### Fixed
- **The addon was not recognized after a manual clone / Addon Manager install**
  ([#4](https://github.com/loobric/loobric-freecad/issues/4),
  [#2](https://github.com/loobric/loobric-freecad/issues/2)). The namespace
  package declaration `freecad/__init__.py` was missing, so FreeCAD never merged
  `freecad.Loobric` into its `freecad` namespace and `init_gui.py` never ran (no
  Loobric button, no preferences). Added it, and fixed `init_gui.py` to import its
  siblings via the package namespace (`from freecad.Loobric import …`) instead of
  bare `import LoobricCommands`, which only worked in a dev layout.
- **Manual-install docs** ([#3](https://github.com/loobric/loobric-freecad/issues/3)):
  added the Windows (`%APPDATA%\FreeCAD\Mod`) and macOS clone paths and a note to
  clone *into* `Mod` and restart FreeCAD.

## [0.2.0] — 2026-06-19

The v2 client: a rebuilt UI on the sectioned schema, importing the `loobric`
reference client instead of a bespoke HTTP layer.

### Added
- **Rebuilt, modeless window with three tabs — Sync · Machines · Audit log.**
  - **Sync**: a plan/apply preview of the local tool directory against the
    server — every tool classified (in sync / changed here / changed on server /
    new / deleted / conflict), with a per-tool-set "set all" cascade, a
    "needs attention" filter, right-click row management (rename, set type,
    delete, link a set to a machine), and in-CAM field-by-field conflict
    resolution.
  - **Machines**: the single binding surface — each machine's tool table with
    the proposal **Inbox folded in** (Confirm / Reject inline, plus Bind
    existing / Bind new / Unbind).
  - **Audit log**: read-only history.
- **Two-way sync**: download (server → FreeCAD) ships alongside upload; synced
  files carry their server identity and round-trip unknown keys (e.g. F&S
  presets) untouched.
- Record inspection as a right-click action on every tab; diagnostics under a
  Debug menu.

### Changed
- **Vendors `loobric.py`** (the stdlib-only MIT reference client); `client.py` is
  now a thin `LoobricApi(loobric.Client)` adapter. **No `requests` dependency and
  no bespoke `LoobricClient`.**
- All UI strings reconciled to the ratified vocabulary (sets, entries, bind /
  bound, Machine).

### Removed
- The previous 7-tab modal dialog and the rejected concepts it carried
  (**Coverage**, **Needs Attention**, **Adopt**); "slot" → **entry**.

### Known issues
- Addon install problems on some setups remain open
  ([#2](https://github.com/loobric/loobric-freecad/issues/2),
  [#4](https://github.com/loobric/loobric-freecad/issues/4)); see the README
  install steps.

[0.2.0]: https://github.com/loobric/loobric-freecad/releases/tag/v0.2.0
