# Changelog

All notable changes to **smooth-freecad** (the FreeCAD CAM client for Smooth) are
recorded here. This project adheres to [Semantic Versioning](https://semver.org/).

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
