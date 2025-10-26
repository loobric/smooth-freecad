# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for FreeCAD tool library (.fctl) file parser.

FreeCAD tool libraries are collections of tool references with tool numbers.

Assumptions:
- .fctl files are valid JSON
- Required fields: version, label, tools
- Tools array contains objects with 'nr' (tool number) and 'path' (filename)
- Path is relative to tool bits directory
- Libraries can be mapped to Smooth ToolSet or ToolPreset collections
"""
import pytest
from pathlib import Path


@pytest.fixture
def fixtures_dir():
    """Return path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def library_dir(fixtures_dir):
    """Return path to libraries fixtures."""
    return fixtures_dir / "libraries"


@pytest.fixture
def bits_dir(fixtures_dir):
    """Return path to bits fixtures."""
    return fixtures_dir / "bits"


@pytest.mark.unit
def test_parse_library_file(library_dir):
    """Test parsing a FreeCAD library file."""
    from clients.freecad.fctl_parser import parse_fctl
    
    fctl_path = library_dir / "f7b13351-b442-479c-a747-630d9ac9d3ce.fctl"
    library = parse_fctl(fctl_path)
    
    assert library["version"] == 1
    assert library["label"] == "default"
    assert "tools" in library
    assert len(library["tools"]) > 0


@pytest.mark.unit
def test_library_tool_entries(library_dir):
    """Test that tool entries have correct structure."""
    from clients.freecad.fctl_parser import parse_fctl
    
    fctl_path = library_dir / "f7b13351-b442-479c-a747-630d9ac9d3ce.fctl"
    library = parse_fctl(fctl_path)
    
    # Check first tool entry
    first_tool = library["tools"][0]
    assert "nr" in first_tool
    assert "path" in first_tool
    assert isinstance(first_tool["nr"], int)
    assert isinstance(first_tool["path"], str)
    assert first_tool["path"].endswith(".fctb")


@pytest.mark.unit
def test_library_tool_numbers(library_dir):
    """Test that tool numbers are present and valid."""
    from clients.freecad.fctl_parser import parse_fctl
    
    fctl_path = library_dir / "f7b13351-b442-479c-a747-630d9ac9d3ce.fctl"
    library = parse_fctl(fctl_path)
    
    tool_numbers = [tool["nr"] for tool in library["tools"]]
    
    # Tool numbers should be positive integers
    assert all(nr > 0 for nr in tool_numbers)
    
    # Check some known tool numbers exist
    assert 1 in tool_numbers
    assert 11 in tool_numbers


@pytest.mark.unit
def test_resolve_tool_paths(library_dir, bits_dir):
    """Test resolving tool bit paths from library references."""
    from clients.freecad.fctl_parser import parse_fctl, resolve_tool_path
    
    fctl_path = library_dir / "f7b13351-b442-479c-a747-630d9ac9d3ce.fctl"
    library = parse_fctl(fctl_path)
    
    # Test resolving a known tool
    first_tool = library["tools"][0]
    resolved_path = resolve_tool_path(first_tool["path"], bits_dir)
    
    # Check if file exists (if fixture file is present)
    if resolved_path.exists():
        assert resolved_path.suffix == ".fctb"
        assert resolved_path.is_file()


@pytest.mark.unit
def test_load_library_with_tools(library_dir, bits_dir):
    """Test loading library and resolving all tool references."""
    from clients.freecad.fctl_parser import parse_fctl, load_library_with_tools
    
    fctl_path = library_dir / "f7b13351-b442-479c-a747-630d9ac9d3ce.fctl"
    library = parse_fctl(fctl_path)
    
    # Load with tool bit resolution
    loaded = load_library_with_tools(library, bits_dir)
    
    assert loaded["label"] == "default"
    assert "tools" in loaded
    
    # Check that tools have been loaded (at least those that exist in fixtures)
    for tool_entry in loaded["tools"]:
        assert "nr" in tool_entry
        assert "path" in tool_entry
        # If tool file exists, should have tool data
        if "tool_data" in tool_entry:
            assert "name" in tool_entry["tool_data"]
            assert "parameters" in tool_entry["tool_data"]


@pytest.mark.unit
def test_library_to_smooth_tool_set(library_dir, bits_dir):
    """Test converting FreeCAD library to Smooth ToolSet format."""
    from clients.freecad.fctl_parser import parse_fctl, library_to_smooth_tool_set
    
    fctl_path = library_dir / "f7b13351-b442-479c-a747-630d9ac9d3ce.fctl"
    library = parse_fctl(fctl_path)
    
    tool_set = library_to_smooth_tool_set(library, bits_dir)
    
    # Check Smooth ToolSet structure
    assert "name" in tool_set
    assert tool_set["name"] == "default"
    assert "tools" in tool_set
    assert isinstance(tool_set["tools"], list)
    
    # Each tool should have smooth format
    if len(tool_set["tools"]) > 0:
        first_tool = tool_set["tools"][0]
        assert "tool_number" in first_tool
        assert "tool_data" in first_tool or "tool_path" in first_tool


@pytest.mark.unit
def test_library_to_smooth_presets(library_dir, bits_dir):
    """Test converting FreeCAD library to Smooth ToolPreset collection."""
    from clients.freecad.fctl_parser import parse_fctl, library_to_smooth_presets
    
    fctl_path = library_dir / "f7b13351-b442-479c-a747-630d9ac9d3ce.fctl"
    library = parse_fctl(fctl_path)
    
    # Convert for a hypothetical machine
    machine_id = "test-machine-001"
    presets = library_to_smooth_presets(library, bits_dir, machine_id)
    
    assert isinstance(presets, list)
    
    # Each preset should have tool_number and machine_id
    for preset in presets:
        assert "tool_number" in preset
        assert "machine_id" in preset
        assert preset["machine_id"] == machine_id


@pytest.mark.unit
def test_error_on_missing_required_fields():
    """Test that missing required fields raise errors."""
    from clients.freecad.fctl_parser import parse_fctl_dict, FctlParseError
    
    invalid = {
        "version": 1,
        "label": "test"
        # Missing tools array
    }
    
    with pytest.raises(FctlParseError) as exc:
        parse_fctl_dict(invalid)
    
    assert "required" in str(exc.value).lower()


@pytest.mark.unit
def test_error_on_invalid_json(tmp_path):
    """Test that invalid JSON raises appropriate error."""
    from clients.freecad.fctl_parser import parse_fctl, FctlParseError
    
    invalid_file = tmp_path / "invalid.fctl"
    invalid_file.write_text("{invalid json")
    
    with pytest.raises(FctlParseError) as exc:
        parse_fctl(invalid_file)
    
    assert "json" in str(exc.value).lower()


@pytest.mark.unit
def test_handle_missing_tool_files(library_dir, tmp_path):
    """Test graceful handling when referenced tool files don't exist."""
    from clients.freecad.fctl_parser import load_library_with_tools, parse_fctl
    
    fctl_path = library_dir / "f7b13351-b442-479c-a747-630d9ac9d3ce.fctl"
    library = parse_fctl(fctl_path)
    
    # Use empty directory for bits (no tool files exist)
    empty_bits_dir = tmp_path / "empty_bits"
    empty_bits_dir.mkdir()
    
    # Should not crash, just mark tools as missing
    loaded = load_library_with_tools(library, empty_bits_dir)
    
    assert "tools" in loaded
    # Tools should be marked as unresolved
    for tool_entry in loaded["tools"]:
        if "tool_data" in tool_entry:
            # If tool data exists, it should be valid
            assert "name" in tool_entry["tool_data"]


@pytest.mark.unit
def test_preserve_original_library_data():
    """Test that original FreeCAD data is preserved for round-trip."""
    from clients.freecad.fctl_parser import parse_fctl_dict
    
    original = {
        "version": 1,
        "label": "test_library",
        "tools": [
            {"nr": 1, "path": "tool1.fctb"},
            {"nr": 2, "path": "tool2.fctb"}
        ]
    }
    
    library = parse_fctl_dict(original)
    
    # Original data should be accessible
    assert library["_original"]["version"] == 1
    assert library["_original"]["label"] == "test_library"
    assert len(library["_original"]["tools"]) == 2


@pytest.mark.unit
def test_tool_number_uniqueness_check():
    """Test checking for duplicate tool numbers in library."""
    from clients.freecad.fctl_parser import check_tool_number_uniqueness
    
    # Valid library with unique tool numbers
    valid_library = {
        "tools": [
            {"nr": 1, "path": "tool1.fctb"},
            {"nr": 2, "path": "tool2.fctb"},
            {"nr": 3, "path": "tool3.fctb"}
        ]
    }
    
    # Should not raise
    duplicates = check_tool_number_uniqueness(valid_library)
    assert len(duplicates) == 0
    
    # Invalid library with duplicate tool numbers
    invalid_library = {
        "tools": [
            {"nr": 1, "path": "tool1.fctb"},
            {"nr": 2, "path": "tool2.fctb"},
            {"nr": 1, "path": "tool3.fctb"}  # Duplicate nr=1
        ]
    }
    
    duplicates = check_tool_number_uniqueness(invalid_library)
    assert len(duplicates) > 0
    assert 1 in duplicates


@pytest.mark.unit
def test_get_tool_by_number(library_dir):
    """Test finding a specific tool by its number."""
    from clients.freecad.fctl_parser import parse_fctl, get_tool_by_number
    
    fctl_path = library_dir / "f7b13351-b442-479c-a747-630d9ac9d3ce.fctl"
    library = parse_fctl(fctl_path)
    
    # Find tool number 1
    tool = get_tool_by_number(library, 1)
    assert tool is not None
    assert tool["nr"] == 1
    
    # Try non-existent tool number
    tool = get_tool_by_number(library, 99999)
    assert tool is None
