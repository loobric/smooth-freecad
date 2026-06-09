# ![Smooth Logo](Resources/icons/Smooth.svg) Smooth - Tool Library Synchronization for FreeCAD

![FreeCAD Version](https://img.shields.io/badge/FreeCAD-0.21+-blue.svg) ![License](https://img.shields.io/badge/License-MIT-green.svg) ![CAM Workbench](https://img.shields.io/badge/CAM-Workbench-orange.svg)

**Keep your tool libraries synchronized across FreeCAD, CNC controllers, and tool management systems**

---

## The Problem

You maintain tool data in multiple places:
- **FreeCAD** for CAM programming
- **CNC Controller** for actual machining (LinuxCNC, etc.)
- **Spreadsheets** for inventory tracking
- **Shop Floor** paper lists
- **Camotics** simulation

When a tool changes (new tool, new insert, wear offset, replacement), you update each system **manually**. 

❌ Errors happen  
❌ Parts get scrapped  
❌ Time is wasted  
❌ Tool data diverges

## The Solution

**Smooth** is a tool synchronization system that keeps your tool libraries in sync - automatically.

```
┌─────────────┐         ┌──────────────┐         ┌─────────────┐
│   FreeCAD   │◄────────┤    Smooth    │────────►│  LinuxCNC   │
│  CAM Tools  │         │ Central Hub  │         │ Tool Table  │
└─────────────┘         └──────────────┘         └─────────────┘
                              ▲
                              │
                        ┌─────┴──────┐
                        │  Web UI    │
                        │ (planned)  │
                        └────────────┘
```

This addon connects FreeCAD's CAM workbench to Smooth, giving you **one-click sync** in both directions.

---

## What Does This Addon Do?

### **Export Tools to Smooth**
- One-click upload of your FreeCAD tool bits
- Share tools across multiple machines and workstations
- Server-side backup of your tool data

### **Import Tools from Smooth**
- Download tool data from the server into FreeCAD
- Keep multiple FreeCAD installations in sync

> **Current limitations (alpha):** custom shape files are *not* uploaded or downloaded —
> tool records reference shapes by path only. Holder/assembly data is not synchronized.
> See the [v2 plan](https://github.com/loobric/smooth-core/issues/3) for the rework that
> addresses these.

### **Version History (server-side)**
- The Smooth server keeps version history and an audit trail of every change
- History browsing from inside the addon is under development

### **Conflict Handling**
- The server rejects stale writes (optimistic locking), so simultaneous edits
  cannot silently overwrite each other
- A guided conflict-resolution flow is planned for the v2 rework — today, a rejected
  sync must be retried after re-importing

---

## Quick Start

### Installation

**Manual Installation** (Addon Manager listing is planned, not yet submitted)
```bash
mkdir -p ~/.local/share/FreeCAD/Mod
git clone https://github.com/loobric/smooth-freecad.git 
```

### First-Time Setup (2 minutes)

**Step 1: Find a Smooth Server**
Run your own Smooth server (see [smooth-core](https://github.com/loobric/smooth-core)). A hosted option is planned but not yet available.

Get the server url and an API key from the server.


**Step 2: Configure FreeCAD**

1. Go to **Edit → Preferences → CAM → Smooth**
2. Enter server URL
3. Enter the API key
4. Click **Test Connection** ✓
5. Click **Apply** to save

**Step 3: Start Syncing!**

1. Switch to **CAM Workbench**
2. Click the **"Sync with Smooth"** button in toolbar  
3. Choose **Export** or **Import**
4. Done! ✅

---

## How It Works

### Exporting Tools (FreeCAD → Smooth)

1. Click **"Sync with Smooth"** button
2. Select **"Export new tools to Smooth"**
3. The addon reads your `.fctb` (tool bits) and `.fctl` (library) files
4. Converts them to Smooth's universal format
5. Uploads to the server (shape files are referenced by path, not uploaded — see limitations above)

**Result:** Your tools are now in the central database, accessible from anywhere!

### Importing Tools (Smooth → FreeCAD)

1. Click **"Sync with Smooth"** button
2. Select **"Import new tools from Smooth"**
3. The addon downloads tool data from Smooth
4. Converts back to FreeCAD format
5. Writes `.fctb` and `.fctl` files
6. Reloads FreeCAD library

**Result:** Your FreeCAD installation has the latest tools from the central database!

### Handling Conflicts

The server uses optimistic locking: if data changed on the server since your last sync,
your push is rejected rather than silently overwriting it. Re-import to pick up the
server state, then re-apply your change. A guided resolution dialog is part of the
planned v2 rework.

---

## Documentation

- **[Technical Documentation](./TECHNICAL.md)** - Developer guide, file formats, architecture
- **[Smooth Homepage](https://loobric.com)** - Learn about the complete Smooth ecosystem
- **[smooth-core](https://github.com/loobric/smooth-core)** - REST API server installation
- **[smooth-linuxcnc](https://github.com/loobric/smooth-linuxcnc)** - LinuxCNC integration
- **[Issue Tracker](https://github.com/loobric/smooth-freecad/issues)** - Report bugs or request features

---

## Contributing

Contributions welcome! This addon is open source (MIT License).

**Ways to contribute:**
- Report bugs or request features via [Issues](https://github.com/loobric/smooth-freecad/issues)
- Improve documentation
- Submit pull requests
- Test with your tool libraries and report compatibility

See [DEVELOPMENT.md](./DEVELOPMENT.md) for developer documentation.

---

## License

MIT License - see [LICENSE](./LICENSE) file

---

## Credits

**Smooth** is developed by the Loobric project team.

- **Homepage:** https://loobric.com
- **GitHub Organization:** https://github.com/loobric
- **Documentation:** https://loobric.com/docs

**Special Thanks:**
- FreeCAD community for the amazing CAM workbench
- ISO 13399 standard for tool data modeling inspiration
- All contributors and testers


**⭐ If you find Smooth useful, please star the project on GitHub! ⭐**

Made with ❤️ for the FreeCAD community
