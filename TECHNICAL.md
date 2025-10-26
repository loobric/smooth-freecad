# Smooth FreeCAD Integration

> Bidirectional tool data synchronization between FreeCAD CAM workbench and Smooth tool management system.

## What is smooth-freecad?

A FreeCAD addon that synchronizes tool data between FreeCAD's CAM workbench and a Smooth server, providing:
- **Import tools from Smooth** - Download tool libraries to FreeCAD
- **Export tools to Smooth** - Upload FreeCAD tools to centralized database
- **Conflict detection** - Warns before overwriting newer versions
- **Version management** - View history, restore previous versions, pull from server
- **Shape file sync** - Upload/download custom tool geometries
- **Bulk operations** - Efficient sync of entire tool libraries

## Features

- Single "Sync with Smooth" button in CAM workbench toolbar
- Preference page in FreeCAD settings (Edit → Preferences → CAM → Smooth)
- Parses `.fctb` (tool bit) and `.fctl` (tool library) files
- Round-trip conversion tested (FreeCAD ↔ Smooth ↔ FreeCAD)
- No separate workbench required - integrates seamlessly with CAM workflow

## Installation

### Option 1: FreeCAD Addon Manager (Future)
Once published:
1. Open FreeCAD
2. Go to Tools → Addon Manager
3. Search for "Smooth"
4. Click Install

### Option 2: Manual Installation
```bash
# Create FreeCAD Mod directory if it doesn't exist
mkdir -p ~/.local/share/FreeCAD/Mod/Smooth

# Copy addon files
cp -r /path/to/smooth-freecad/* ~/.local/share/FreeCAD/Mod/Smooth/
```

### Option 3: Symlink (for Development)
```bash
ln -s /path/to/smooth-freecad ~/.local/share/FreeCAD/Mod/Smooth
```

**Restart FreeCAD** after installation.

## Quick Start

### 1. Configure Connection

1. Open FreeCAD
2. Go to Edit → Preferences → CAM → Smooth
3. Enter your Smooth server URL (e.g., `http://localhost:8000`)
4. (Optional) Enter API key if authentication is enabled
5. Click "Test Connection" to verify
6. Click "Apply" to save

Configuration is stored in `~/.config/smooth/freecad.json`.

### 2. Sync Tools

1. Switch to **CAM workbench** in FreeCAD
2. Click the **"Sync with Smooth"** button in toolbar (or press Ctrl+Shift+S)
3. Choose sync operation:
   - **Export new tools to Smooth** - Send FreeCAD tools to server
   - **Import new tools from Smooth** - Receive tools from server
   - **Update modified tools** (future feature)
4. Click "Start Sync"
5. Monitor progress in dialog
6. Review summary of operations

### 3. Handle Conflicts

If the server has a newer version than your local library:
1. **Conflict warning dialog** appears with options:
   - **YES** - Force push (overwrite server with your version)
   - **NO** - Choose which version to restore
   - **CANCEL** - Abort export
2. If choosing version, **version selector** shows all versions with:
   - Timestamps
   - Change summaries
   - Option to restore any version or pull latest
3. After pulling server version, local `.fctl` files update automatically

## File Locations

FreeCAD stores tool data in:
```
~/.local/share/FreeCAD/v1-1/CamAssets/Tools/
├── Bit/       # Tool bit files (.fctb)
├── Library/   # Tool library files (.fctl)
└── Shape/     # Custom shape files (.FCStd, STEP, STL, etc.)
```

## How It Works

### FreeCAD → Smooth

1. **Parse** - Read `.fctb` (tool bits) and `.fctl` (tool libraries)
2. **Convert** - Transform to Smooth data model:
   - Tool bits → `ToolItem` entities
   - Tool libraries → `ToolSet` entities with `ToolPreset` members
3. **Upload** - Send to Smooth via REST API
4. **Sync shapes** - Upload custom shape files (built-in shapes skipped)

### Smooth → FreeCAD

1. **Download** - Fetch `ToolSet` and associated `ToolItem` entities
2. **Convert** - Transform to FreeCAD format:
   - `ToolItem` → `.fctb` files
   - `ToolSet` → `.fctl` files
3. **Write** - Save to FreeCAD directories
4. **Sync shapes** - Download custom shape files
5. **Reload** - Update FreeCAD library list

### Data Preservation

Round-trip conversion is fully tested:
- Units preserved ("5.00 mm", "60.00°")
- Parameter names converted (snake_case ↔ CamelCase)
- Shape references maintained
- Comments and metadata retained

## Requirements

- **FreeCAD** 0.21 or later
- **Python requests** library (usually included with FreeCAD)
- **Smooth server** running and accessible

## Architecture

```
FreeCAD CAM Workbench
       │
       ├─ Tool Bits (.fctb files)
       ├─ Tool Libraries (.fctl files)
       └─ Shape Files (.FCStd, etc.)
              │
              ▼
    ┌─────────────────────┐
    │  Smooth FreeCAD     │
    │  (Client-side)      │
    │  • Parse formats    │
    │  • Convert data     │
    │  • Sync via API     │
    └─────────────────────┘
              │
              ▼ REST API
    ┌─────────────────────┐
    │   Smooth Core       │
    │   (Server)          │
    │   • ToolItem        │
    │   • ToolSet         │
    │   • ToolPreset      │
    └─────────────────────┘
```

**Client-side conversion** keeps Smooth Core application-agnostic.

## Configuration

Edit `~/.config/smooth/freecad.json`:
```json
{
  "api_url": "http://localhost:8000",
  "api_key": "your-api-key-here",
  "auto_sync": false
}
```

**Settings:**
- `api_url` - Smooth server URL
- `api_key` - Optional API key for authentication
- `auto_sync` - Future feature for automatic sync

## Testing

### Unit Tests
```bash
# Install dependencies
pip install pytest pytest-cov

# Run all tests
pytest tests/

# Run specific test suite
pytest tests/test_fctb_parser.py
pytest tests/test_fctl_parser.py
pytest tests/test_fctb_export.py
pytest tests/test_fctl_export.py

# With coverage
pytest --cov=. --cov-report=html
```

**Test coverage:** 53/53 tests passing

### Sample Data

Test files included:
- `sample_tools/test_drill_5mm.fctb` - 5mm HSS drill bit
- `sample_tools/test_endmill_6mm.fctb` - 6mm carbide endmill
- `sample_tools/test_library.fctl` - Sample library with multiple tools

### Manual Testing

```bash
# Test tool bit parsing
python fctb_parser.py sample_tools/test_drill_5mm.fctb

# Test library parsing
python fctl_parser.py sample_tools/test_library.fctl
```

## Troubleshooting

**Addon not appearing in FreeCAD:**
- Check installation path: `~/.local/share/FreeCAD/Mod/Smooth/`
- Verify `InitGui.py` and `package.xml` exist
- Restart FreeCAD completely
- Check FreeCAD Python console for errors

**Sync button not in toolbar:**
- Ensure CAM workbench is active
- Wait a few seconds for toolbar injection
- Check FreeCAD Python console for errors

**Connection errors:**
- Verify Smooth server is running: `curl http://localhost:8000/api/health`
- Check API URL in preferences
- Test network connectivity
- Verify firewall settings

**Tool data mismatch:**
- Check unit consistency (mm vs inches)
- Verify shape files are accessible
- Review FreeCAD console for parsing errors
- Test round-trip conversion separately

**Version conflicts:**
- Check server version history in web interface
- Pull latest version before making local changes
- Use "Force push" carefully - it overwrites server data

## Development

See [DEVELOPMENT.md](./DEVELOPMENT.md) for:
- Component architecture details
- File format specifications
- Testing procedures
- Contributing guidelines

## Documentation

- **[DEVELOPMENT.md](./DEVELOPMENT.md)** - Development guide
- **[../DEVELOPMENT.md](../DEVELOPMENT.md)** - Cross-project principles
- **FreeCAD Docs** - https://wiki.freecad.org/Path_Workbench

## Contributing

Contributions welcome! Please:
1. Test with real FreeCAD tool libraries
2. Ensure round-trip conversion works
3. Follow TDD (tests before implementation)
4. Update docstrings
5. Follow functional programming style

## License

[License information to be added]

## Support

- GitHub Issues: [Link to issues]
- FreeCAD Forum: [Link to thread]
- Documentation: [Link to docs]
