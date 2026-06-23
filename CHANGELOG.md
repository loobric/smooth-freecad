# Changelog

All notable changes to **smooth-freecad** (the FreeCAD CAM client for Smooth) are
recorded here. This project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.1] — 2026-06-23

### Fixed
- A plain download no longer triggers a phantom "local is newer — upload"
  prompt that forced a second apply. The client now records its section baseline
  on pull, so a download converges in a single apply (download is symmetric with
  upload).

## [0.3.0] — 2026-06-21

The **M2** client: browse the server's catalog records and create tools from
them. Tracks smooth-core 0.2.0.

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
- **Re-vendored `loobric.py`** from smooth-core 0.2.0 (the M2 reference client):
  `create_instance_from_catalog`, `add_to_set` / `remove_from_set`,
  `show-tool-set`, `server_version`. Backwards-compatible — every existing call
  site is unchanged.

## [0.2.1] — 2026-06-20

### Fixed
- **The addon was not recognized after a manual clone / Addon Manager install**
  ([#4](https://github.com/loobric/smooth-freecad/issues/4),
  [#2](https://github.com/loobric/smooth-freecad/issues/2)). The namespace
  package declaration `freecad/__init__.py` was missing, so FreeCAD never merged
  `freecad.Smooth` into its `freecad` namespace and `init_gui.py` never ran (no
  Smooth button, no preferences). Added it, and fixed `init_gui.py` to import its
  siblings via the package namespace (`from freecad.Smooth import …`) instead of
  bare `import SmoothCommands`, which only worked in a dev layout.
- **Manual-install docs** ([#3](https://github.com/loobric/smooth-freecad/issues/3)):
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
  now a thin `SmoothApi(loobric.Client)` adapter. **No `requests` dependency and
  no bespoke `SmoothClient`.**
- All UI strings reconciled to the ratified vocabulary (sets, entries, bind /
  bound, Machine).

### Removed
- The previous 7-tab modal dialog and the rejected concepts it carried
  (**Coverage**, **Needs Attention**, **Adopt**); "slot" → **entry**.

### Known issues
- Addon install problems on some setups remain open
  ([#2](https://github.com/loobric/smooth-freecad/issues/2),
  [#4](https://github.com/loobric/smooth-freecad/issues/4)); see the README
  install steps.

[0.2.0]: https://github.com/loobric/smooth-freecad/releases/tag/v0.2.0
