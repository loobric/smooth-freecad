# Loobric for FreeCAD — Technical Notes

How FreeCAD's CAM tool files map onto the Loobric **sectioned** tool schema, and
how the addon is put together. For contributor workflow and tests, see
[DEVELOPMENT.md](./DEVELOPMENT.md).

## The sectioned schema in one paragraph

Every entity on the server is one record with three sections:

- **internal** — server-owned identity and bookkeeping (`id`, `version`,
  timestamps). Clients never write it.
- **canonical** — the agreed truth. Each leaf is provenance-tagged
  (`{value, source}`), so the server can say which client or human asserted a
  given fact. Canonical facts are changed deliberately through the **assert**
  door, not by routine sync.
- **clients** — a map of per-client sections, each with an opaque `data` payload
  the client owns. FreeCAD's section key is `freecad`. A routine sync writes only
  `clients.freecad.data`; the server rejects a body that tries to touch internal
  or canonical.

The translation between FreeCAD files and this schema lives in
`freecad/Loobric/mapping.py` (pure functions, no FreeCAD imports).

## How FreeCAD concepts map

| FreeCAD | Loobric | Notes |
|---|---|---|
| Tool bit (`.fctb`) | **ToolInstanceRecord** | The full original document rides verbatim in `clients.freecad.data.fctb` (lossless — unknown keys such as FreeCAD's feeds-and-speeds presets survive a round trip). Shape/type and dimensions are surfaced as canonical asserts. |
| Tool library (`.fctl`) | **ToolSet** | The label and format version stay in `clients.freecad.data`. Per-tool numbers are promoted out into canonical `members`, because a set's numbering is shared truth. |
| Tool number (in a library) | number on a **ToolSet member** | The `nr` is the CAM side's durable **claim** — the T-number posted G-code will call. The machine never overwrites it: when a machine runs the set as its active setup (`loobric use-set`), the server reports how reality compares (`satisfied` / `requested` / `mismounted` / `blocked` / `pending bind`) alongside the untouched claim, and the sync view shows it. A member with no claim adopts the machine's observed number on pull — the observation is its first known number. |
| Machine tool table | **Machine** with **ToolTableEntry** rows | Lives on the Machines surface. An entry can be bound to a ToolInstanceRecord. |
| (an entry pointing at a tool) | **Binding** | Created by Confirm proposal, Bind existing, or Bind new (mint). |
| Shape type (`endmill`, `drill`, …) | canonical `geometry.shape` | FreeCAD is the client whose scope permits asserting `geometry.shape`. |
| Tool dimensions | canonical `geometry.*` | Surfaced as asserts; a value that can't be determined is simply not asserted (nothing is fabricated). |
| Tool name / label | canonical `name` | |
| File path / library path | server `internal.id` | Persisted back into the file as an additive `loobric.record_id` key. |
| CAM Job (its ToolControllers) | **ToolSet** (job-derived) | Built by the `Loobric_SetFromJob` command; each `ToolController.ToolNumber` becomes the member's claim. Deliberately has **no `.fctl`** — `clients.freecad.data` carries `{origin: "job", document, job}` instead of a library payload, which is what makes the sync plan render it read-only (`job_set`) rather than as a downloadable server-only library. Identity for re-runs is the Job's `LoobricSetId` property (addon-added, stored in the `.FCStd`); the provenance stamp is the fallback matcher. |

Concepts FreeCAD does not currently use: holder/assembly modeling and tool-usage
tracking exist in Loobric but are not synced from this client.

## Identity (never create duplicates)

- After first contact, the server's `internal.id` is written back into the
  `.fctb` / `.fctl` as an additive `loobric.record_id` key (older readers ignore
  it). Re-sync writes that id's sections instead of blind-creating.
- The id is written back immediately after a create, so a crashed sync never
  double-creates.
- If a FreeCAD editor dropped the `loobric` key on save (its tool editors discard
  unknown top-level keys), the record is re-matched by the server-held
  `client_item_id` (the verbatim `.fctb` id, or the `.fctl` label), then by
  filename — never by display name.
- Tool bits sync before libraries, because a set's membership needs record ids.

## Lossless round trips

A parameter string from the original file is kept verbatim unless its canonical
value actually differs, so formatting quirks (for example `3.175 mm`) do not
churn on every sync.

## Module layout

All logic that can be tested without FreeCAD is kept free of FreeCAD and PySide
imports, so the bulk of the addon runs headless under pytest.

| File (`freecad/Loobric/`) | Role | FreeCAD/Qt imports? |
|---|---|---|
| `mapping.py` | FreeCAD `.fctb` / `.fctl` <-> sectioned schema translation | No |
| `sync.py` | Plan/apply engine: diff a local tool directory against the server, then upload/download per the chosen decisions | No |
| `client.py` | `LoobricApi`, a thin `loobric.Client` subclass that adds FreeCAD's identity (`freecad`, `human@freecad`), the sync-lane helpers, `ping`, and a recording transport for the API log | No |
| `loobric.py` | Vendored single-file reference Python client (stdlib only). The one place HTTP transport and the API surface are defined | No |
| `viewmodel.py` | Pure view-model: functions that answer "what should the window show" (the Sync plan tree, the checkbox apply model, the Machines tables) | No |
| `jobset.py` | Job → ToolSet planning and apply: resolve a Job's tools into member claims (refusals, clash interlock, drift flags), then create/adopt/update the server-side set | No |
| `mirrorsync.py` | Unattended sync policy for the asset store: a refresh applies every unambiguous direction (deletions propagate, via `.trash/` with retention); only conflicts are held for the Sync window. Also: per-server mirror keying, read-only policy, missing-shape detection | No |
| `mirrorlock.py` | The mirror's sync-ownership lock: one FreeCAD instance reconciles, others ride along; a crashed owner's lock is stolen via PID + heartbeat staleness | No |
| `syncworker.py` | The one background worker: debounced pushes (~5 s), periodic pulls (~10 min), immediate manual refresh — store calls never do HTTP on the GUI thread | No |
| `assetstore.py` | `LoobricAssetStore`, swapped in as the CAM asset system's `"local"` store (FreeCAD 1.1+): tool bits/libraries served from a server-reconciled mirror, everything else delegated to the real FileStore; activation split into thread-safe `prepare()` (key introspection → read-only mode, lock, initial reconcile) and GUI-thread `complete()` (worker, store swap), plus the state the status UI renders | FreeCAD (no Qt) |
| `statuscolor.py` | State snapshot → toolbar icon color (green in sync / yellow activating-syncing / red held / gray offline; held outranks all) + SVG tinting | No |
| `storemode.py` | Store-mode orchestration: the opt-in preference, auto-activation on CAM workbench activation (after camassets setup), non-blocking activation (prepare on a background thread, completion trampolined to the GUI thread), the one-time import review | Yes |
| `LoobricStatusWidget.py` | The status UI: the status-bar widget (starting / idle / syncing / N held / offline + read-only and ride-along badges; click-through to the filtered Sync window) and the toolbar-button status coloring | Yes |
| `LoobricDialog.py` | The modeless window shell: three tabs over one shared client, plus the Debug menu | Yes |
| `LoobricTabs.py` | The Sync, Machines, and Audit tab widgets; renders the view-model and fires actions | Yes |
| `LoobricCommands.py` | Registers the `Loobric_Sync`, `Loobric_Configure`, and `Loobric_SetFromJob` CAM commands (plus the job-set confirmation dialog) | Yes |
| `LoobricPreferences.py` | The CAM preference page (server URL, API key, Test Connection) | Yes |
| `init_gui.py` | Addon entry point: registers the commands, the preference page, and the toolbar/menu manipulator | Yes |

The client is built on the vendored `loobric.py` rather than a hand-rolled HTTP
client. There is no `requests` dependency and no bespoke `LoobricClient` class.

## Errors

`client.py` re-exports loobric's typed error hierarchy. `LoobricError` is an alias
of `loobric.LoobricError`, so `except LoobricError` catches every client failure.
HTTP failures are `loobric.HTTPError` carrying a `.status` — the UI distinguishes
a 409 (for example, binding a tool that is already bound elsewhere, which it
offers to move).

## Experimental: the Loobric asset store (0.6.0)

FreeCAD 1.1's CAM asset system resolves toolbits, libraries, shapes, and
machines through named `AssetStore` backends; the stock `"local"` store is a
`FileStore` over the user's asset directory. Store mode swaps in a
Loobric-backed store under the same name via the public
`cam_assets.register_store()` API — no monkeypatching, no UI replacement. The
NATIVE tool library dock, selectors, and editors then show and edit
server-backed content, and sync becomes largely invisible.

Activation: the opt-in preference (Edit → Preferences → CAM → Loobric) makes
store mode auto-start whenever the CAM workbench activates — always AFTER
`camassets.ensure_assets_initialized()`, so the stock library seeding of an
empty local store can never be mistaken for user tools and pushed. The
`Loobric_AssetStore` command toggles it for one session. The first activation
against a mirror offers a one-time import review of the user's existing local
tools (the ordinary Sync checkbox UI pointed at the real local dir).

Activation never blocks the GUI (0.6.2): the slow half — key introspection,
lock acquisition, the initial reconcile (a full library download on a
first-ever run) — is `assetstore.prepare()`, run by `storemode` on a
background thread while the status UI shows *activating*; the fast half —
starting the worker and the `register_store()` swap — is
`assetstore.complete()`, trampolined back to the GUI thread over a queued Qt
signal (the swap changes what the native tool UI reads, so it must not race
it). Exactly one activation runs at a time (`begin_activation()` claims the
slot); a failure lands in `abort_activation()` and the state returns to
inactive. `assetstore.activate()` remains as the synchronous composition for
non-GUI callers.

Design:

- **Reads never touch the network.** The store serves `toolbit` and
  `toolbitlibrary` assets from a PER-SERVER mirror directory
  (`~/.config/loobric/freecad_mirror/<host>-<hash>/`, an ordinary `Bit/` +
  `Library/` tools dir). All other asset types (shapes, icons, machines)
  pass through to the original local FileStore. `.fcm` machine pass-through
  is PERMANENT policy — a FreeCAD machine config (post/kinematics) is a
  different concept from a Loobric Machine.
- **Writes land in the mirror first** and poke the one background worker
  (`syncworker.py`), which debounces pushes, pulls periodically, and honors
  manual refreshes — through the existing plan/apply engine (same identity
  and assert discipline as the Sync tab). Server unreachable → the change is
  safe in the mirror and uploads on the next successful pass.
- **Deletions propagate both ways, with a net**: every file the policy
  removes goes to the mirror's `.trash/` first (30-day retention). Only
  CONFLICTS are held (`mirrorsync.SAFE_DECISIONS` / `HELD_ACTIONS`) — the
  Sync tab, retargeted at the mirror in store mode, is where humans decide.
- **Read-only keys fail fast.** Activation introspects the key
  (`GET /auth/key`, server ≥ 0.8.0): a read-only key means writes raise at
  the edit and the status widget shows the badge — an edit never lands
  unpushable in the mirror.
- **One instance owns sync per mirror** (`mirrorlock.py`: PID + heartbeat
  lock, stale locks stolen); other FreeCAD instances ride along.
- **The status UI** (`LoobricStatusWidget.py`) is where invisible sync stays
  visible, twice over: the status-bar widget (starting / idle / syncing /
  N held / offline + badges; clicking opens the Sync window filtered to what
  needs a decision) and the Loobric toolbar button itself, whose whole icon
  recolors with sync status (`statuscolor.py` maps state → color and tints
  the SVG source: green in sync, yellow activating/syncing, red held
  conflicts — which outrank everything — gray offline; the brand color when
  store mode is off).

Not synced: custom shape `.fcstd` contents (missing shapes are named in the
log after a pull; shape blob sync is the designed follow-up — loobric-server
#45). FreeCAD 1.0 has no asset system: the addon degrades to the classic
explicit Sync window.

## FreeCAD file format notes

- `.fctb` (tool bit) and `.fctl` (tool library) are JSON.
- Dimensions embed units as strings (`"5.00 mm"`, `"60.00°"`), not bare numbers.
- Parameter names are CamelCase in the files.
- A bit references its shape by a `.fcstd` shape file (built-in or custom). Only
  the reference is synced; custom shape file contents are not transferred.
- A library references its tool bits by relative path and carries each tool's
  number.
