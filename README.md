# Smooth for FreeCAD

Synchronize FreeCAD's CAM tool libraries with a Smooth tool-data server, so the
same tools stay consistent across FreeCAD, CNC controllers, and other shop
systems.

This addon is the FreeCAD client for **Smooth**. It adds a "Smooth" button to
the CAM workbench and a preference page for server settings. It does not create
a separate workbench.

> Alpha software. The data model and UI are still settling as part of the v2
> rework. Expect rough edges.

## The problem

Tool data usually lives in several places at once: FreeCAD (for CAM
programming), the CNC controller (LinuxCNC and others), spreadsheets, and
shop-floor lists. When a tool changes, every copy has to be updated by hand, the
copies drift apart, and the mismatches cause scrapped parts and wasted time.

Smooth keeps one server-side source of truth and syncs the clients to it. This
addon connects FreeCAD's CAM workbench to that server.

## Requirements

- FreeCAD 1.1 or later, with the CAM workbench
- A running Smooth server, self-hosted (see
  [smooth-core](https://github.com/loobric/smooth-core)) or hosted
- No extra Python packages. The client is standard-library only; it vendors
  `loobric.py` (the single-file reference Python client) and uses nothing beyond
  what FreeCAD already ships.

## Install

Addon Manager listing is planned but not yet submitted, so install manually by
cloning into FreeCAD's `Mod` directory:

```bash
# Linux
git clone https://github.com/loobric/smooth-freecad.git \
  ~/.local/share/FreeCAD/Mod/smooth-freecad
```

On other platforms, clone into the `Mod` directory shown in FreeCAD at
**Edit -> Preferences -> General -> (paths)**, then restart FreeCAD.

## Configure

1. Open **Edit -> Preferences -> CAM -> Smooth**.
2. Enter the server URL and your API key (get both from your Smooth server).
3. Click **Test Connection**, then **Apply**.

Settings are stored in `~/.config/smooth/freecad.json`.

## Using it

Switch to the **CAM** workbench and click the **Smooth** button. A modeless
window opens (FreeCAD stays usable beside it) with three tabs.

### Sync

The one place tools move between FreeCAD and the server. It shows a tree of every
tool set and its tools, comparing your local tool directory against the server.
Each changed row gets a direction:

- upload local -> server
- download server -> local
- leave unsynced

A **Needs attention only** filter hides everything that is already in sync. Set a
direction on a tool-set (folder) row to apply it to every tool inside at once,
then click **Apply Selected**. Nothing touches disk or the server until you
apply.

When a tool changed on both sides, double-click the row to see a field-by-field,
side-by-side comparison and choose **Keep Local**, **Keep Server**, or **Skip**.
The server marks each canonical field with the side that changed it.

Right-click a row for management actions: inspect the record, rename, set tool
type, delete on the server, or link a tool set to a machine.

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

- [TECHNICAL.md](./TECHNICAL.md) — data model and how the FreeCAD formats map to
  the Smooth schema
- [DEVELOPMENT.md](./DEVELOPMENT.md) — contributor guide, layout, and tests
- [smooth-core](https://github.com/loobric/smooth-core) — the Smooth server
- [smooth-linuxcnc](https://github.com/loobric/smooth-linuxcnc) — LinuxCNC client
- [Issue tracker](https://github.com/loobric/smooth-freecad/issues)

## License

MIT — see [LICENSE](./LICENSE).

Developed by the Loobric project team. Thanks to the FreeCAD community for the
CAM workbench and to the ISO 13399 standard for tool-data modeling.
