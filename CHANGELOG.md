# Changelog

All notable changes to **loobric-freecad** (the FreeCAD CAM client for Loobric) are
recorded here. This project adheres to [Semantic Versioning](https://semver.org/).

## [0.6.2] — 2026-08-03

Startup responsiveness and at-a-glance sync status, from real-world use of
store mode: activation used to block the CAM UI while the mirror synced —
worst on a first run, when the whole library downloads.

### Changed
- **Asset-store activation no longer blocks the GUI.** The slow half
  (server key introspection, sync-lock acquisition, the initial mirror
  reconcile) now runs on a background thread (`assetstore.prepare()`);
  the store swap and worker start (`assetstore.complete()`) return to the
  GUI thread over a queued Qt signal, since swapping what the native tool
  UI reads must not race it. The status widget shows "starting…" while it
  runs, FreeCAD stays usable throughout, and the toggle command's summary
  dialog now arrives when activation completes. Exactly one activation can
  be in flight; failures return the state to inactive and surface per
  entry point (dialog for the manual toggle, console for auto-start).

### Added
- **The Loobric toolbar button shows sync status by color.** The whole
  icon recolors — green (in sync), yellow (starting or syncing), red (held
  conflicts need a decision — outranks everything else), gray (offline,
  working from the mirror); the normal brand color while store mode is
  off. The colored variants are tinted from the SVG source at runtime (no
  per-color icon files); the mapping lives in `statuscolor.py` (pure,
  tested, pinned to the shipped artwork's fill). The icon is re-stamped on
  every workbench switch because that is when FreeCAD (re)builds toolbars.
- The status-bar widget gained the matching "Loobric: starting…" state
  with a tooltip explaining that FreeCAD stays usable during the fill.

## [0.6.1] — 2026-08-03

Point fix for the 0.6.0 asset-store mirror-layout defect, found in real-world
testing: the store built its path mapping from CAM's user store (which nests
tool files under `Tools/Bit` and `Tools/Library`) while the sync side
reconciles `Bit/` and `Library/`. The mirror split into two disjoint trees —
FreeCAD's native UI couldn't see server tools, and tools created in FreeCAD
were stranded unpushed while the Sync dialog reported everything in sync.

### Fixed
- **The asset store now uses the mirror's own layout** — one definition
  (`mirrorsync.MIRROR_MAPPING`) shared by the store and the sync policy, so
  the two sides can never disagree on where tool files live.
- **One-time mirror migration on activation.** A stray `Tools/` subtree
  from 0.6.0 is folded back into the real tree: stranded writes move in
  (and upload on the next refresh), identical duplicates are dropped, and a
  name that diverged on both sides keeps the server-synced copy with the
  other recoverable from the mirror's trash — nothing silently disappears.
- **Stock-library seeding can no longer be pushed to the server.** The
  store's `is_empty` never reports tool types empty: CAM's asset
  initialization re-runs from UI entry points long after activation (the
  activation-ordering guard demonstrably failed), and would otherwise seed
  the stock library into the mirror as if the user created those tools. An
  empty Loobric account now stays empty.

## [0.6.0] — 2026-08-01 (needs loobric-server ≥ 0.8.0 for the read-only-key badge)

The asset-store release (design grilled & ratified 2026-08-01): on FreeCAD
1.1+, the NATIVE tool library becomes the Loobric interface, with sync running
invisibly in the background. Labeled experimental.

### Added
- **EXPERIMENTAL: the Loobric asset store.** Swaps FreeCAD 1.1's `"local"`
  CAM asset store for a Loobric-backed one via the public
  `cam_assets.register_store()` API — the native tool library dock,
  selectors, and editors show and edit server-backed tools. Reads come from
  a local mirror, so browsing works offline; a write lands in the mirror and
  is pushed by the background worker. Enable it per FreeCAD in
  Edit → Preferences → CAM → Loobric ("Serve the CAM tool library from
  Loobric") — it then auto-activates whenever the CAM workbench comes up
  (after camassets setup, so the stock library seeding is never pushed) —
  or toggle it for one session with the "Loobric asset store (experimental)"
  CAM command.
- **One background sync worker** (`syncworker.py`): writes are debounced
  (~5 s of quiet, coalesced) and pushed; a periodic pass (~10 min) pulls
  server-side changes; manual refresh is immediate. Store reads and writes
  NEVER do HTTP on the GUI thread; if the server is unreachable, changes are
  safe in the mirror and upload on the next successful pass.
- **A status-bar widget** is the one place invisible sync stays visible:
  idle / syncing… / "N held" / offline, plus read-only and ride-along
  badges. Clicking it opens the Loobric window — pre-filtered to the items
  needing a decision when anything is held.
- **Per-server mirrors.** Each server URL gets its own mirror under
  `~/.config/loobric/freecad_mirror/<host>-<hash>/`, so switching servers
  can never cross-pollinate tool data.
- **Deletions now propagate — with a net.** A tool deleted in FreeCAD's
  editors deletes the server record on the next pass, and a record deleted
  on the server removes the mirror file; in both flows the mirror file goes
  to `.trash/` first (30-day retention). Only CONFLICTS are held for the
  Sync window — the one thing sync never decides is a both-sides edit.
- **Read-only keys fail fast.** At activation the addon asks the server what
  the key may do (`GET /auth/key`, loobric-server #44). A read-only key
  (including a pre-0.6.0 legacy key) activates the store READ-ONLY: browsing
  and pulls work, editing fails immediately at the edit with a clear message
  + a badge — an edit can never land unpushable in the mirror. Older servers
  without the endpoint: unknown, treated as writable (the server still
  enforces).
- **One-time import review.** The first activation against a mirror offers
  to open the Sync window against FreeCAD's REAL local tool directory, so
  existing local tools get a reviewed, checkbox-chosen import instead of a
  silent bulk upload.
- **Two seats, one sync.** A lock file (`sync.lock`, PID + heartbeat) makes
  one FreeCAD instance the mirror's sync owner; a second instance rides
  along (reads and writes work; the owner reconciles for both). A crashed
  owner's lock is stolen after its heartbeat goes stale.
- **Missing custom shapes are a warning, not a mystery.** Shape file
  contents are still not synced; after a pull, bits referencing a shape this
  machine doesn't have are named in the log with what's missing. Shape blob
  sync is the designed follow-up (loobric-server #45).
- Vendored client: `key_info()` (credential introspection, server ≥ 0.8.0).

### Changed
- **The Sync tab retargets itself in store mode**: it plans against the
  MIRROR (what FreeCAD is actually showing) instead of FreeCAD's own tool
  directory, making it the resolution surface for held conflicts. On
  FreeCAD 1.0 (no asset system) everything works as before — the classic
  Sync window against the local tools dir is the way to work.

### Not synced, by design
- **`.fcm` machine configs pass through untouched — permanently.** A FreeCAD
  machine definition (postprocessing/kinematics) is a different concept from
  a Loobric Machine; the asset store will never bridge them.
- Custom shape `.fcstd` contents (warning + follow-up above); job-derived
  sets remain server-only (no `.fctl` materialization).

## [0.5.0] — 2026-07-30

### Changed
- **Sync tab: the checkbox apply model replaces the per-row direction
  dropdowns.** A row's ↑/↓ action is now DERIVED from its status (the Status
  and Action columns can never disagree); the user only chooses inclusion via
  a checkbox, and the Apply button states its plan ("Apply (3 uploads,
  2 downloads)"). A 📁 set node carries a tri-state checkbox that toggles all
  its tools — replacing the self-resetting "Set all in set…" menu-combo.
  "All: Suggested"/"All: Skip" became "Check all"/"Uncheck all".
- **Conflicts and deletions have no checkbox.** Rows with no safe default are
  structurally different now: their action reads "resolve…" and double-click
  opens the side-by-side resolution (which, as before, applies immediately —
  the one deliberate exception to "nothing happens until Apply"). Deletion
  resolutions got concrete labels ("Delete on server too" / "Restore from
  server" instead of Keep Local/Keep Server).
- **Forcing the opposite direction is a context-menu act.** "Force download
  (discard local changes)" / "Force upload (overwrite server changes)" live in
  the row's right-click menu, flip the row's action label, and check it —
  deliberate, invisible in the common path.

### Added
- **Create tool set from job.** A new CAM workbench command (next to the
  Loobric window command) that creates or updates a Loobric tool set from the
  active Job's tools — each ToolController's number becomes the member's
  durable claim, closing the "job numbers connect to Loobric only by
  convention" gap for the job that matters. Deliberately writes **no `.fctl`**:
  the user's FreeCAD tool libraries are never touched. Rules: tools not saved
  to the tool library are refused (reported in the confirmation); the same
  tool under two numbers is refused; two different tools claiming one number
  block the command (the job as posted cannot run); never-synced member bits
  are uploaded as part of the one confirmation; a job copy that drifted from
  its library tool is included with a ⚠. Re-runs replace members wholesale
  (the job is the source of truth) — identity rides the Job's new
  `LoobricSetId` property (in the `.FCStd`), with the set's job-provenance
  stamp (`clients.freecad.data.origin = "job"`) as the fallback matcher, and
  a name collision with an unrelated set re-prompts instead of silently
  minting a near-duplicate.
- **Job-derived sets render read-only in the Sync tab** (action `job_set`,
  📋): setup status stays visible where the programmer works, but there is no
  download direction — the old "server only" default would have materialized
  exactly the `.fctl` this feature promises not to create.
- `sync.upload_bit` — upload one `.fctb` outside a full apply (create or
  update), with the same identity write-back and journal discipline as the
  apply path. Used by the job-set flow for never-synced members.

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
