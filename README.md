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

### Sync

The one place tools move between FreeCAD and the server. It shows a tree of every
tool set and its tools, comparing your local tool directory against the server.
Each changed row gets a direction:

- upload local -> server
- download server -> local
- leave unsynced

An **Out of sync only** filter hides everything that is already in sync. Set a
direction on a tool-set (folder) row to apply it to every tool inside at once,
then click **Apply Selected**. Nothing touches disk or the server until you
apply.

When a tool changed on both sides, double-click the row to see a field-by-field,
side-by-side comparison and choose **Keep Local**, **Keep Server**, or **Skip**.
The server marks each canonical field with the side that changed it.

Right-click a row for management actions: inspect the record, rename, set tool
type, delete on the server, or link a tool set to a machine.

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
  FreeCAD shapes resolve normally on both ends.
- Tool holder / assembly data is not modeled or synchronized.
- After a download, reload the CAM tool library to see the changes in FreeCAD's
  editors.

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
