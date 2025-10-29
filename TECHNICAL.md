# Smooth FreeCAD Integration

## Concept Map
| FreeCAD Concept | Smooth Concept | Description | Notes |
|-----------------|----------------|-------------|-------|
| **Tool Bit (.fctb)** | **ToolItem** | Represents a physical cutting tool with specific geometry and properties | - FreeCAD stores as JSON files with shape-specific parameters<br>- Smooth uses a unified ToolItem with tool_type and geometry fields |
| **Tool Library (.fctl)** | **ToolSet** | Collection of tools with additional metadata | - FreeCAD libraries reference tool bits by file path<br>- Smooth ToolSets contain direct references to ToolItems |
| **Tool Number** | **ToolPreset** | Machine-specific tool assignment | - FreeCAD stores this in the library file<br>- Smooth separates machine-specific data into ToolPresets |
| **Shape Type** | **tool_type** | Classification of the tool | - FreeCAD has shape types like "endmill", "drill", etc.<br>- Smooth uses a standardized enum for tool types |
| **Tool Parameters** | **geometry** | Dimensional and geometric properties | - FreeCAD parameters are shape-specific<br>- Smooth normalizes common parameters in a standard structure |
| **Material** | **material** | Tool material properties | - FreeCAD stores as string (e.g., "HSS", "Carbide")<br>- Smooth uses a structured material object |
| **Vendor** | **manufacturer** | Tool manufacturer information | - FreeCAD may include in description<br>- Smooth has dedicated manufacturer fields |
| **Tool Holder** | **ToolAssembly** | Combination of holder and insert | - FreeCAD handles this implicitly<br>- Smooth models it explicitly as a separate entity |
| **Tool Table** | **ToolPreset** | Machine-specific tool configuration | - FreeCAD tool libraries can function as tool tables<br>- Smooth uses ToolPresets for machine-specific configs |
| **Shape File** | **geometry.shape** | 3D model of the tool | - FreeCAD uses .FCStd, STEP, or STL files<br>- Smooth references shape files in geometry data |
| **Tool Length** | **geometry.overall_length** | Total length of the tool | - Handled consistently in both systems<br>- Smooth includes units explicitly |
| **Cutting Diameter** | **geometry.cutting_diameter** | Effective cutting diameter | - Core parameter in both systems<br>- Smooth enforces unit consistency |
| **Flute Length** | **geometry.cutting_length** | Length of cutting edges | - Common parameter for end mills and drills<br>- Smooth uses more generic field names |
| **Shank Diameter** | **geometry.shank_diameter** | Diameter of tool shank | - Important for tool holder compatibility<br>- Smooth includes in geometry object |
| **Tool Description** | **description** | Human-readable tool description | - FreeCAD often includes specs in description<br>- Smooth separates specs into structured fields |
| **Tool Label** | **name** | Display name of the tool | - FreeCAD uses filename or internal name<br>- Smooth enforces unique naming |
| **Version** | **version** | Revision tracking | - FreeCAD may track in filename<br>- Smooth uses explicit version field for concurrency |
| **Tool Path** | **id** | Unique identifier | - FreeCAD uses file paths<br>- Smooth uses UUIDs |
| **Tool Library Path** | **ToolSet.id** | Reference to tool collection | - FreeCAD uses filesystem paths<br>- Smooth uses UUID references |
| **Tool Life** | **ToolUsage** | Usage tracking | - Basic in FreeCAD<br>- Comprehensive in Smooth with tracking and prediction |


* Smooth Assemblies are unused by FreeCAD
* Smooth Instances are unused by FreeCAD

## Key Differences

1. **Data Organization**:
   - FreeCAD: File-based with loose coupling between tools and libraries
   - Smooth: Database-backed with explicit relationships
   - FreeCAD doesn't support assemblies yet.

2. **Extensibility**:
   - FreeCAD: Shape-based with fixed parameter sets per shape
   - Smooth: Flexible schema with tool_type-specific validation

3. **Multi-Machine Support**:
   - FreeCAD: Single machine configuration per library
   - Smooth: Multiple machine configurations through ToolPresets

4. **Versioning**:
   - FreeCAD: Manual versioning through filenames
   - Smooth: Built-in version tracking and conflict resolution

5. **Metadata**:
   - FreeCAD: Limited metadata in JSON
   - Smooth: Rich metadata with audit logging and relationships
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

TBD

