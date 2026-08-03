# Loobric for FreeCAD

Synchronize FreeCAD's CAM tool libraries with a Loobric tool-data server, so the
same tools stay consistent across FreeCAD, CNC controllers, and other shop
systems.

This addon is the FreeCAD client for **Loobric**. It adds a "Loobric" button to
the CAM workbench and a preference page for server settings. It does not create
a separate workbench.

> Alpha software. The data model and UI are still settling as part of the v2
> rework. Expect rough edges.

## The problem

Tool data usually lives in several places at once: FreeCAD (for CAM
programming), the CNC controller (LinuxCNC and others), spreadsheets, and
shop-floor lists. When a tool changes, every copy has to be updated by hand, the
copies drift apart, and the mismatches cause scrapped parts and wasted time.

Loobric keeps one server-side source of truth and syncs the clients to it. This
addon connects FreeCAD's CAM workbench to that server.

## Requirements

- FreeCAD 1.1 or later, with the CAM workbench
- A running Loobric server, self-hosted (see
  [loobric-server](https://github.com/loobric/loobric-server)) or hosted
- No extra Python packages. The client is standard-library only; it vendors
  `loobric.py` (the single-file reference Python client) and uses nothing beyond
  what FreeCAD already ships.

## Install

Addon Manager listing is planned but not yet submitted, so install manually by
cloning into FreeCAD's `Mod` directory, then **restart FreeCAD**:

```bash
# Linux
git clone https://github.com/loobric/loobric-freecad.git \
  ~/.local/share/FreeCAD/Mod/loobric-freecad
```

```bat
REM Windows (run in Command Prompt)
git clone https://github.com/loobric/loobric-freecad.git ^
  "%APPDATA%\FreeCAD\Mod\loobric-freecad"
REM %APPDATA% expands to C:\Users\<YourUser>\AppData\Roaming
```

```bash
# macOS
git clone https://github.com/loobric/loobric-freecad.git \
  ~/Library/Application\ Support/FreeCAD/Mod/loobric-freecad
```

The exact `Mod` path for your install is shown in FreeCAD at
**Edit -> Preferences -> General -> (paths)**. Clone *into* `Mod` so the result
is `Mod/loobric-freecad/package.xml` (not `Mod/package.xml`), then restart
FreeCAD. After restart you should see a **Loobric** button in the CAM workbench's
Tool Commands toolbar and a **Loobric** page under Preferences -> CAM. Requires
FreeCAD 1.1 or later.

## Configure

1. Open **Edit -> Preferences -> CAM -> Loobric**.
2. Enter the server URL and your API key (get both from your Loobric server).
3. Click **Test Connection**, then **Apply**.

Settings are stored in `~/.config/loobric/freecad.json`.

### Try against the sandbox

No server of your own? Use the free hosted sandbox:

1. In **Preferences -> CAM -> Loobric**, set the server URL to
   `https://api.loobric.com` (it's the default).
2. Create an account and an API key with the Python client, then paste the key
   into the preference page:
   ```bash
   pip install loobric-cli
   export LOOBRIC_BASE_URL=https://api.loobric.com
   loobric register you@example.com
   loobric login you@example.com
   loobric create-key freecad --scopes "read write"   # paste the printed key into FreeCAD
   ```
3. Click **Test Connection**, then **Apply**.

The sandbox is a shared playground — **data may be reset, so keep nothing real
there.** Full walkthrough:
[loobric-cli/docs/SANDBOX.md](https://github.com/loobric/loobric-cli/blob/master/docs/SANDBOX.md).

## Using it

Switch to the **CAM** workbench and click the **Loobric** button. A modeless
window opens (FreeCAD stays usable beside it) with four tabs — Sync, Catalog,
Machines, and Audit log.

### Asset store mode (experimental, FreeCAD 1.1+)

The 0.6.0 way to work: enable **"Serve the CAM tool library from Loobric"**
in Edit → Preferences → CAM → Loobric, and FreeCAD's own tool library dock
and editors show and edit server-backed tools — no separate sync ritual.
Reads come from a local mirror (browsing works offline); your edits upload in
the background a few seconds after you make them, and server-side changes
arrive on their own every few minutes. Starting up never blocks the CAM UI:
the initial fill (a full library download on a first run) happens in the
background while FreeCAD stays usable. Sync health is always one glance away
— the Loobric toolbar button changes color with sync status (green = in
sync, yellow = starting or syncing, red = a conflict needs your decision,
gray = offline, working from the mirror), and a status-bar widget spells out
the same state;
when something needs a decision (a tool changed on both sides), clicking the
widget opens the Sync window filtered to exactly those items. Deleting a
tool in FreeCAD deletes it on the server too — a copy is kept in the mirror's
trash for 30 days. The first activation offers a one-time review of your
existing local tools so you choose what gets imported. To try it for a single
session instead, use the **Loobric asset store (experimental)** command in
the CAM menu. On FreeCAD 1.0 everything below works as before.

### Sync

The one place tools move between FreeCAD and the server. It shows a tree of every
tool set and its tools, comparing your local tool directory against the server.
Each changed row's ↑ upload / ↓ download action follows directly from its
status — you only choose what to include, with a checkbox. Rows with a safe
direction come pre-checked; the checkbox on a tool-set (folder) row toggles
every tool inside it, and the Apply button always states exactly what it will
do ("Apply (3 uploads, 2 downloads)"). Nothing touches disk or the server
until you press it.

Rows with no safe default — a tool changed on **both** sides, or a deletion —
have no checkbox. Double-click one to see a field-by-field, side-by-side
comparison (the server marks each canonical field with the side that changed
it) and choose a side; that choice applies immediately. To deliberately go
against a row's suggested direction (say, discard local edits and take the
server copy), use **Force download** / **Force upload** in the right-click
menu.

An **Out of sync only** filter hides everything that is already in sync.
Right-click a row for management actions: inspect the record, rename, set tool
type, delete on the server, or check a tool set's setup status.

A tool set created with **Create tool set from job** (see below) appears here
read-only (📋): its setup status is visible, but it has no download action —
it deliberately has no library file.

### Create tool set from job

The **Create tool set from job** command (next to the Loobric button) builds a
Loobric tool set from the active Job's tools: each ToolController's tool
number becomes the member's claim — the number your posted G-code will call.
One confirmation shows the members, anything that can't be included (tools not
saved to the tool library, or one tool used under two numbers — fix the job
and re-run), tools that will be uploaded first, and a ⚠ when the job's copy of
a tool differs from the library file. Two different tools claiming the same
number stop the command: that job cannot run as posted.

No FreeCAD tool library (`.fctl`) is written — your libraries stay yours. The
set lives on the server; re-running the command updates it wholesale from the
job (the link is stored on the Job as a `LoobricSetId` property, so it travels
inside the `.FCStd`).

### Catalog

Browse the server's catalog records — name, manufacturer, product code,
geometry, and provenance source — read-only. Pick one and **Create tool from
catalog** makes an unbound tool on the server from that type and immediately
materializes a local `.fctb` in your active tools library, pre-filled from the
catalog's nominal geometry and linked back to the new tool. It lands already in
sync. Reload the CAM tool library to see it in the editor.

### Machines

The one place tool-table bindings happen. Each machine shows its tool-table
entries. An unbound entry that the server has a proposal for shows the proposed
tool inline; **Confirm** binds it, **Reject** dismisses it. You can also bind an
existing tool into an entry, **Bind new** to mint a new tool from the entry and
bind it, or **Unbind**. Entries and machines can be deleted here too.

### Audit log

A read-only view of recent operations recorded by the server: who changed what,
when, and the result.

### Debug

The **Debug** menu (bottom of the window) opens a live API log of the client's
HTTP traffic. Inspecting a record's raw JSON is a right-click action on any row.

## Current limitations (alpha)

- Custom shape files (`.fcstd` tool geometries) are not themselves transferred —
  tools reference shapes by name, and only the reference travels. Built-in
  FreeCAD shapes resolve normally on both ends. In asset store mode, a pulled
  tool whose custom shape is missing on this machine is named in the report
  view (shape sync is a planned follow-up).
- FreeCAD machine configs (`.fcm`) are never synced — by design, permanently.
  They define postprocessing and simulation kinematics, a different concept
  from a Loobric Machine.
- Tool holder / assembly data is not modeled or synchronized.
- After a download, reload the CAM tool library to see the changes in FreeCAD's
  editors.
- A Job's `ToolController.ToolNumber` is a copy taken when the tool is added
  to the Job; a `.fctl`-synced set carries the *library* number, not the Job's
  copy. **Create tool set from job** closes this gap for the job that matters
  (its claims come from the ToolControllers themselves), but a post-time
  check against the active setup is still planned, not yet built.

## Links

- [CONTRIBUTING.md](./CONTRIBUTING.md) — how to contribute (MIT, DCO sign-off, no CLA)
- [TECHNICAL.md](./TECHNICAL.md) — data model and how the FreeCAD formats map to
  the Loobric schema
- [DEVELOPMENT.md](./DEVELOPMENT.md) — contributor guide, layout, and tests
- [loobric-server](https://github.com/loobric/loobric-server) — the Loobric server
- [loobric-linuxcnc](https://github.com/loobric/loobric-linuxcnc) — LinuxCNC client
- [Issue tracker](https://github.com/loobric/loobric-freecad/issues)

## License

MIT — see [LICENSE](./LICENSE). Contributions welcome under DCO sign-off — see
[CONTRIBUTING.md](./CONTRIBUTING.md) (no CLA).

Developed by the Loobric project team. Thanks to the FreeCAD community for the
CAM workbench and to the ISO 13399 standard for tool-data modeling.
