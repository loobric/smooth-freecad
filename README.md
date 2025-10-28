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

## 🎯 The Problem

You maintain tool data in multiple places:
- **FreeCAD** for CAM programming
- **CNC Controller** for actual machining (LinuxCNC, etc.)
- **Spreadsheets** for inventory tracking
- **Shop Floor** paper lists

When a tool changes (new insert, wear offset, replacement), you update each system **manually**. 

❌ Errors happen  
❌ Parts get scrapped  
❌ Time is wasted  
❌ Tool data diverges

## ✅ The Solution

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

## ✨ What Does This Addon Do?

### 📤 **Export Tools to Smooth**
- One-click upload of your FreeCAD tool library
- Share tools across multiple machines and workstations
- Automatic backup of your tool data
- Custom shape files uploaded automatically

### 📥 **Import Tools from Smooth**  
- Download standardized tool libraries to FreeCAD
- Get updates when tools are modified elsewhere
- Keep multiple FreeCAD installations in sync
- Pull changes from CNC controllers or other sources

### 🔄 **Version Control**
- Track changes to your tool libraries over time
- Restore previous versions if something goes wrong
- See who changed what and when
- Complete audit trail for quality control

### ⚠️ **Conflict Detection**
- Warns before overwriting newer tool data
- Choose which version to keep
- Never lose work due to simultaneous edits
- Smart merge options (coming soon)

---

## 🚀 Quick Start

### Installation

**Via Addon Manager** (Recommended)
1. Open FreeCAD
2. Go to **Tools → Addon Manager**
3. Search for **"Smooth"**
4. Click **Install**
5. Restart FreeCAD

**Manual Installation**
```bash
mkdir -p ~/.local/share/FreeCAD/Mod/Smooth
cp -r smooth-freecad/* ~/.local/share/FreeCAD/Mod/Smooth/
```

### First-Time Setup (2 minutes)

**Step 1: Start Smooth Server** (if you don't have one already)

```bash
# Quick install
pip install smooth-core
smooth-server start

# Or see full setup guide:
# https://github.com/loobric/smooth-core
```

**Step 2: Configure FreeCAD**

1. Go to **Edit → Preferences → CAM → Smooth**
2. Enter server URL: `http://localhost:8000`
3. (Optional) Enter API key if authentication is enabled
4. Click **Test Connection** ✓
5. Click **Apply** to save

**Step 3: Start Syncing!**

1. Switch to **CAM Workbench**
2. Click the **"Sync with Smooth"** button in toolbar  
   (or press `Ctrl+Shift+S`)
3. Choose **Export** or **Import**
4. Done! ✅

---

## 🎬 How It Works

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

## 🎨 Features in Detail

### Seamless CAM Integration
- No separate workbench - integrates directly into CAM
- Single toolbar button for all operations
- Keyboard shortcut: `Ctrl+Shift+S`
- Progress indicators during sync
- Summary of operations after completion

### Smart File Handling
- Parses `.fctb` (tool bit) files
- Parses `.fctl` (tool library) files
- Handles shape files (`.FCStd`, STEP, STL, etc.)
- Preserves units ("5.00 mm", "60.00°")
- Maintains comments and metadata
- Round-trip tested (no data loss)

### Network Resilience
- Automatic retry on network errors
- Graceful handling of server downtime
- Offline mode (view cached data)
- Batch uploads for efficiency

### Security & Multi-User
- Optional API key authentication
- User-based data isolation
- Machine-specific restrictions
- Audit logging of all changes

---

## 📖 Documentation

- **[Technical Documentation](./TECHNICAL.md)** - Developer guide, file formats, architecture
- **[Smooth Homepage](https://loobric.com)** - Learn about the complete Smooth ecosystem
- **[smooth-core](https://github.com/loobric/smooth-core)** - REST API server installation
- **[smooth-linuxcnc](https://github.com/loobric/smooth-linuxcnc)** - LinuxCNC integration
- **[Issue Tracker](https://github.com/loobric/smooth-freecad/issues)** - Report bugs or request features

---

## 🎥 Video Demos

*(Coming soon - video tutorials showing installation and usage)*

---

## 💡 Use Cases

### Small Shop with Multiple Machines
- Maintain one master tool library in FreeCAD
- Export to Smooth central server
- Each CNC controller pulls latest tool table
- Update tool offset → syncs everywhere automatically

### Job Shop / Contract Manufacturing
- Import customer tool libraries to FreeCAD
- Program CAM with their exact tooling
- Export completed programs with tool data
- Customer imports into their CNC controllers

### Multi-User CAM Programming
- Team shares tool library via Smooth
- Each programmer has synced FreeCAD installation
- Tool changes propagate to all team members
- Version control prevents conflicts

### Tool Inventory Management
- Track physical tool locations via Web UI
- FreeCAD sees which tools are available
- Program only with in-stock tools
- Update tool status from shop floor

---

## 🛠️ Requirements

- **FreeCAD** 0.21 or later
- **Python** (included with FreeCAD)
- **Smooth Server** running and accessible (see [smooth-core](https://github.com/loobric/smooth-core))
- Network connection to Smooth server

---

## 🤝 Contributing

Contributions welcome! This addon is open source (MIT License).

**Ways to contribute:**
- Report bugs or request features via [Issues](https://github.com/loobric/smooth-freecad/issues)
- Improve documentation
- Submit pull requests
- Test with your tool libraries and report compatibility

See [DEVELOPMENT.md](./DEVELOPMENT.md) for developer documentation.

---

## 📄 License

MIT License - see [LICENSE](./LICENSE) file

---

## 🙏 Credits

**Smooth** is developed by the Loobric project team.

- **Homepage:** https://loobric.com
- **GitHub Organization:** https://github.com/loobric
- **Documentation:** https://loobric.com/docs

**Special Thanks:**
- FreeCAD community for the amazing CAM workbench
- ISO 13399 standard for tool data modeling inspiration
- All contributors and testers

---

## 📬 Support

- **Issues:** https://github.com/loobric/smooth-freecad/issues
- **Website:** https://loobric.com
- **Email:** support@loobric.com

---

<p align="center">
  <b>⭐ If you find Smooth useful, please star the project on GitHub! ⭐</b>
</p>

<p align="center">
  Made with ❤️ for the FreeCAD community
</p>
