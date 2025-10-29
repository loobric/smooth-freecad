# ![Smooth Logo](Resources/icons/Smooth.svg) Smooth - Tool Library Synchronization for FreeCAD

<p align="center">
  <img src="https://img.shields.io/badge/FreeCAD-0.21+-blue.svg" alt="FreeCAD Version">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
  <img src="https://img.shields.io/badge/CAM-Workbench-orange.svg" alt="CAM Workbench">
</p>

<p align="center">
  <b>Keep your tool libraries synchronized across FreeCAD, CNC controllers, and tool management systems</b>
</p>

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
                        │ Management │
                        └────────────┘
```

This addon connects FreeCAD's CAM workbench to Smooth, giving you **one-click sync** in both directions.

---

## What Does This Addon Do?

### **Export Tools to Smooth**
- One-click upload of your FreeCAD tool library
- Share tools across multiple machines and workstations
- Automatic backup of your tool data
- Custom shape files uploaded automatically

### **Import Tools from Smooth**  
- Download standardized tool libraries to FreeCAD
- Get updates when tools are modified elsewhere
- Keep multiple FreeCAD installations in sync
- Pull changes from CNC controllers or other sources

### **Version Control**
- Track changes to your tool libraries over time
- Restore previous versions if something goes wrong
- See who changed what and when
- Complete audit trail for quality control

### **Conflict Detection**
- Warns before overwriting newer tool data
- Choose which version to keep
- Never lose work due to simultaneous edits
- Smart merge options (coming soon)

---

## Quick Start

### Installation

**Via Addon Manager** (Recommended)
1. Open FreeCAD
2. Go to **Tools → Addon Manager**
3. Search for **"Smooth"**
4. Click **Install**
5. Restart FreeCAD

**Manual Installation**
```bash
mkdir -p ~/.local/share/FreeCAD/Mod
git clone https://github.com/loobric/smooth-freecad.git 
```

### First-Time Setup (2 minutes)

**Step 1: Find a Smooth Server**
You can self host a smooth server of get a free account at [Loobric](https://loobric.com)

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
5. Uploads to the server
6. Custom shape files are uploaded automatically

**Result:** Your tools are now in the central database, accessible from anywhere!

### Importing Tools (Smooth → FreeCAD)

1. Click **"Sync with Smooth"** button
2. Select **"Import new tools from Smooth"**
3. The addon downloads tool data from Smooth
4. Converts back to FreeCAD format
5. Writes `.fctb` and `.fctl` files
6. Downloads custom shape files
7. Reloads FreeCAD library

**Result:** Your FreeCAD installation has the latest tools from the central database!

### Handling Conflicts

If someone else updated a tool library while you were working:

1. **Conflict Warning** dialog appears
2. Choose:
   - **Force Push** - Overwrite server with your version
   - **Choose Version** - View history and pick which to keep
   - **Cancel** - Stop and resolve manually
3. If you choose version, see full history with timestamps
4. Select version to restore
5. Sync completes successfully

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


<p align="center">
  <b>⭐ If you find Smooth useful, please star the project on GitHub! ⭐</b>
</p>

<p align="center">
  Made with ❤️ for the FreeCAD community
</p>
