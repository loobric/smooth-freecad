# Smooth FreeCAD Integration Development

This document contains development information specific to **smooth-freecad** - the FreeCAD CAM workbench integration for Smooth tool synchronization.

# AI PROMPT

AI agents working on **any** Smooth repository should incorporate the following prompts into their responses:

1. Favor a functional style of programming over an object-oriented style.
2. Docstrings will be included for every function, class, and module. Docstrings should accurately document the assumptions of the code. If those assumptions change, the docstring MUST be updated accordingly. Do NOT change the docstrings without confirming with the user that the change is intentional.
3. Unit testing is required for all code. Minimize the need for mocks and stubs. If mocks or stubs are required, document the assumptions in the docstring.
4. Unit testing should focus on testing the assumptions of the code. If those assumptions change, the unit tests MUST be updated accordingly. Do NOT change the unit tests without confirming with the user that the change is intentional.
5. Changes should be incremental and minimal. Avoid large refactoring changes unless explicitly requested by the user.
6. Favor TDD (Test Driven Development). Write tests first and confirm with the user that they are complete BEFORE implementing the code.
7. Keep README and DEVELOPMENT files up to date.
8. Regularly reread this prompt and the design philosophy to ensure that the code is consistent with the overall design.

## Project Overview

The FreeCAD integration provides:
- Bidirectional tool data synchronization between FreeCAD and Smooth
- Format translators for `.fctb` (tool bit) and `.fctl` (tool library) files
- FreeCAD addon with GUI for sync operations
- Shape file handling for custom tool geometries
- Conflict detection and version management

## Components

### Format Translators

#### 1. Tool Bit Parser (`fctb_parser.py`)
Handles `.fctb` (tool bit) files - individual tool definitions.

**Functions:**
- `parse_fctb(filepath)` - Parse tool bit file → dict
- `fctb_to_smooth(fctb_data)` - Convert to Smooth ToolItem format
- `smooth_to_fctb(tool_item)` - Convert Smooth ToolItem → .fctb format
- `write_fctb(filepath, fctb_data)` - Write tool bit file

**Key Features:**
- Unit-aware parsing (extracts "5.00 mm", "60.00°")
- Shape type mapping (Drill, Endmill, Ballend, VBit, etc.)
- Preserves original data for round-trip conversion
- Parameter name conversion (snake_case ↔ CamelCase)

#### 2. Tool Library Parser (`fctl_parser.py`)
Handles `.fctl` (tool library) files - collections of tools.

**Functions:**
- `parse_fctl(filepath)` - Parse library file → dict
- `fctl_to_smooth(fctl_data)` - Convert to Smooth ToolSet/ToolPreset format
- `smooth_to_fctl(toolset)` - Convert Smooth ToolSet → .fctl format
- `write_fctl(filepath, fctl_data)` - Write library file
- `export_toolset_to_freecad(toolset, output_dir)` - Complete export with all tool bits

**Key Features:**
- Resolves tool bit references
- Maps tool numbers to ToolPresets
- Validates tool number uniqueness
- Handles machine-specific libraries

#### 3. Shape File Handler (`shape_storage.py`)
Manages FreeCAD shape files (.FCStd, STEP, STL, etc.).

**Functions:**
- `upload_shape_file(filepath)` - Base64 encode and hash
- `download_shape_file(shape_data, output_path)` - Decode and write
- `verify_shape_hash(filepath, expected_hash)` - SHA256 verification

**Key Features:**
- Base64 encoding for inline storage
- SHA256 hash verification
- Path resolution across directories
- Skips built-in FreeCAD shapes

**Total Test Coverage: 53/53 tests passing**

### Future Enhancements
- [ ] Change Notification
- [ ] Improved UI
- [ ] Incremental sync (only changed tools)
- [ ] Auto-sync on library changes
- [ ] Tool usage tracking integration
- [ ] Advanced conflict resolution (merge)

## File Locations

## Testing

### Unit Tests
```bash
# Run all FreeCAD integration tests
pytest tests/

# Run specific test suite
pytest tests/test_fctb_parser.py
pytest tests/test_fctl_parser.py
pytest tests/test_fctb_export.py
pytest tests/test_fctl_export.py

# Run with verbose output
pytest -v
```

### Manual Testing

1. **Test Tool Bit Parsing:**
```bash
python fctb_parser.py sample_tools/test_drill_5mm.fctb
```

2. **Test Library Parsing:**
```bash
python fctl_parser.py sample_tools/test_library.fctl
```

3. **Test Addon in FreeCAD:**
- Install addon (manual or symlink)
- Open FreeCAD
- Switch to CAM workbench
- Look for "Sync with Smooth" button
- Click and test sync operations

## Project Structure

```
smooth-freecad/
├── InitGui.py              # Addon initialization
├── SmoothCommands.py       # Command registration
├── SmoothDialog.py         # Sync dialog UI
├── SmoothPreferences.py    # Preference page
├── fctb_parser.py          # Tool bit format handler
├── fctl_parser.py          # Library format handler
├── shape_storage.py        # Shape file management
├── package.xml             # Addon metadata
├── Resources/              # Icons and resources
│   └── icons/
├── sample_tools/           # Sample data for testing
│   ├── test_drill_5mm.fctb
│   ├── test_endmill_6mm.fctb
│   └── test_library.fctl
├── tests/                  # Test suite
│   ├── fixtures/           # Test data
│   ├── test_fctb_parser.py
│   ├── test_fctl_parser.py
│   ├── test_fctb_export.py
│   └── test_fctl_export.py
├── README.md               # User documentation
└── DEVELOPMENT.md          # This file
```

## Dependencies

- FreeCAD 1.0 or later
- Python `requests` library (usually bundled with FreeCAD)
- Access to running Smooth server

For development/testing:
- pytest
- pytest-cov

## Contributing

When contributing to smooth-freecad:
1. Follow TDD - write tests first
2. Ensure round-trip conversion works (FreeCAD → Smooth → FreeCAD)
3. Test with real FreeCAD tool libraries
4. Update docstrings for format-specific assumptions
5. Maintain compatibility with FreeCAD's file format versions
6. Follow functional programming style (see ../DEVELOPMENT.md)

## FreeCAD File Format Notes

### Tool Bit Format (.fctb)
- JSON-based format
- Includes embedded units ("5.00 mm", not just 5.0)
- Parameter names use CamelCase
- Shape references can be built-in or custom files
- Version field tracks FreeCAD format version

### Tool Library Format (.fctl)
- JSON-based format
- References tool bits by relative path
- Contains tool numbers and names
- Version field tracks FreeCAD format version

### Shape Files
- FreeCAD document files (.FCStd)
- Can also be STEP, STL, or other CAD formats
- Custom shapes stored separately from built-in shapes
- Path resolution is relative to CamAssets directory

## Round-Trip Conversion

Ensuring data integrity through round-trip:

```
FreeCAD .fctb → Smooth ToolItem → FreeCAD .fctb
```

All tests verify that:
1. Original data is preserved
2. Units are maintained
3. Parameter names convert correctly
4. Shape references resolve properly
5. JSON structure matches FreeCAD's expectations

## Troubleshooting

**Addon not appearing in FreeCAD:**
- Check installation path: `~/.local/share/FreeCAD/Mod/Smooth/`
- Verify `InitGui.py` and `package.xml` exist
- Restart FreeCAD completely

**Sync button not in toolbar:**
- Ensure CAM workbench is active
- Check FreeCAD Python console for errors
- Verify QTimer initialization completed

**Connection errors:**
- Test Smooth server: `curl http://localhost:8000/api/health`
- Check API URL in preferences
- Verify network connectivity

**Tool data mismatch:**
- Check unit consistency (mm vs inches)
- Verify shape files are accessible
- Review round-trip test results
