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
| `viewmodel.py` | Pure view-model: functions that answer "what should the window show" (the Sync plan tree, the Machines tables) | No |
| `LoobricDialog.py` | The modeless window shell: three tabs over one shared client, plus the Debug menu | Yes |
| `LoobricTabs.py` | The Sync, Machines, and Audit tab widgets; renders the view-model and fires actions | Yes |
| `LoobricCommands.py` | Registers the `Loobric_Sync` and `Loobric_Configure` CAM commands | Yes |
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

## FreeCAD file format notes

- `.fctb` (tool bit) and `.fctl` (tool library) are JSON.
- Dimensions embed units as strings (`"5.00 mm"`, `"60.00°"`), not bare numbers.
- Parameter names are CamelCase in the files.
- A bit references its shape by a `.fcstd` shape file (built-in or custom). Only
  the reference is synced; custom shape file contents are not transferred.
- A library references its tool bits by relative path and carries each tool's
  number.
