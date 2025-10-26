# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for FreeCAD tool bit (.fctb) file parser.

FreeCAD tool bits are JSON files with embedded units in parameter values.

Assumptions:
- .fctb files are valid JSON
- Required fields: version, name, shape, shape-type, parameter
- Parameter values include units as strings (e.g., "5.00 mm", "60.00°")
- Different shape-types have different parameter sets
- attribute field is optional/empty dict
"""
import pytest
import json
from pathlib import Path


@pytest.fixture
def fixtures_dir():
    """Return path to test fixtures directory."""
    return Path(__file__).parent / "fixtures" / "bits"


@pytest.mark.unit
def test_parse_drill_bit(fixtures_dir):
    """Test parsing a simple drill bit file."""
    from clients.freecad.fctb_parser import parse_fctb
    
    fctb_path = fixtures_dir / "drill_5.0mm.fctb"
    tool = parse_fctb(fctb_path)
    
    assert tool["version"] == 2
    assert tool["id"] == "drill_5.0mm"
    assert tool["name"] == "Drill 5.0mm"
    assert tool["shape"] == "drill.fcstd"
    assert tool["shape_type"] == "Drill"
    
    # Check parameter parsing
    params = tool["parameters"]
    assert params["Diameter"]["value"] == 5.0
    assert params["Diameter"]["unit"] == "mm"
    assert params["Length"]["value"] == 85.0
    assert params["Length"]["unit"] == "mm"
    assert params["Material"] == "HSS"
    assert params["TipAngle"]["value"] == 119.0
    assert params["TipAngle"]["unit"] == "°"


@pytest.mark.unit
def test_parse_endmill_bit(fixtures_dir):
    """Test parsing an endmill bit with more complex parameters."""
    from clients.freecad.fctb_parser import parse_fctb
    
    fctb_path = fixtures_dir / "end_mill_6.0mm_2f.fctb"
    tool = parse_fctb(fctb_path)
    
    assert tool["shape_type"] == "Endmill"
    assert tool["parameters"]["Flutes"] == 2
    assert tool["parameters"]["CuttingEdgeHeight"]["value"] == 14.0
    assert tool["parameters"]["ShankDiameter"]["value"] == 6.0


@pytest.mark.unit
def test_parse_ballend_bit(fixtures_dir):
    """Test parsing a ball end mill."""
    from clients.freecad.fctb_parser import parse_fctb
    
    fctb_path = fixtures_dir / "ball_nose_end_mill_6.0mm_2f.fctb"
    tool = parse_fctb(fctb_path)
    
    assert tool["shape_type"] == "Ballend"
    assert tool["parameters"]["Diameter"]["value"] == 6.0


@pytest.mark.unit
def test_parse_vbit(fixtures_dir):
    """Test parsing a V-bit with angle parameters."""
    from clients.freecad.fctb_parser import parse_fctb
    
    fctb_path = fixtures_dir / "60degree_Vbit.fctb"
    tool = parse_fctb(fctb_path)
    
    assert tool["shape_type"] == "VBit"
    params = tool["parameters"]
    assert params["CuttingEdgeAngle"]["value"] == 60.0
    assert params["CuttingEdgeAngle"]["unit"] == "°"
    assert params["TipDiameter"]["value"] == 0.1


@pytest.mark.unit
def test_parse_thread_mill(fixtures_dir):
    """Test parsing a thread mill with specialized parameters."""
    from clients.freecad.fctb_parser import parse_fctb
    
    fctb_path = fixtures_dir / "thread_mill_6.0mm.fctb"
    tool = parse_fctb(fctb_path)
    
    assert tool["shape_type"] == "ThreadMill"
    params = tool["parameters"]
    assert params["Crest"]["value"] == 0.10
    assert params["NeckDiameter"]["value"] == 4.10
    assert params["NeckLength"]["value"] == 20.0


@pytest.mark.unit
def test_parse_unit_variations():
    """Test parsing different unit formats."""
    from clients.freecad.fctb_parser import parse_parameter_value
    
    # Test various unit formats
    assert parse_parameter_value("5.00 mm") == {"value": 5.0, "unit": "mm"}
    assert parse_parameter_value("119.00°") == {"value": 119.0, "unit": "°"}
    assert parse_parameter_value("60.0000 °") == {"value": 60.0, "unit": "°"}
    assert parse_parameter_value("0.1000 mm") == {"value": 0.1, "unit": "mm"}
    
    # Test values without units (strings)
    assert parse_parameter_value("HSS") == "HSS"
    assert parse_parameter_value("Forward") == "Forward"
    
    # Test numeric values (no units)
    assert parse_parameter_value(2) == 2
    assert parse_parameter_value(0) == 0


@pytest.mark.unit
def test_handle_zero_chipload():
    """Test that zero chipload is handled correctly."""
    from clients.freecad.fctb_parser import parse_parameter_value
    
    result = parse_parameter_value("0.00 mm")
    assert result["value"] == 0.0
    assert result["unit"] == "mm"


@pytest.mark.unit
def test_convert_to_smooth_tool_item(fixtures_dir):
    """Test converting FreeCAD tool bit to Smooth ToolItem format."""
    from clients.freecad.fctb_parser import parse_fctb, fctb_to_smooth
    
    fctb_path = fixtures_dir / "end_mill_6.0mm_2f.fctb"
    tool = parse_fctb(fctb_path)
    smooth_tool = fctb_to_smooth(tool)
    
    # Check Smooth format
    assert "type" in smooth_tool  # Should map from shape_type
    assert smooth_tool["description"] == "End Mill 6.0mm 2F"
    assert "geometry" in smooth_tool
    assert smooth_tool["geometry"]["diameter"] == 6.0
    assert smooth_tool["geometry"]["diameter_unit"] == "mm"
    assert smooth_tool["geometry"]["flutes"] == 2
    
    # Material should be extracted
    assert smooth_tool["material"]["type"] == "HSS"


@pytest.mark.unit
def test_shape_type_mapping():
    """Test that FreeCAD shape types map to Smooth types."""
    from clients.freecad.fctb_parser import map_shape_type
    
    assert map_shape_type("Drill") == "cutting_tool"
    assert map_shape_type("Endmill") == "cutting_tool"
    assert map_shape_type("Ballend") == "cutting_tool"
    assert map_shape_type("VBit") == "cutting_tool"
    assert map_shape_type("ThreadMill") == "cutting_tool"
    # Add more specific mappings if needed


@pytest.mark.unit
def test_missing_optional_fields():
    """Test handling of missing optional fields."""
    from clients.freecad.fctb_parser import parse_fctb_dict
    
    # Minimal valid tool bit
    minimal = {
        "version": 2,
        "name": "Test Tool",
        "shape": "test.fcstd",
        "shape-type": "Drill",
        "parameter": {
            "Diameter": "5.00 mm"
        }
    }
    
    tool = parse_fctb_dict(minimal)
    assert tool["version"] == 2
    assert "attribute" in tool  # Should default to empty dict


@pytest.mark.unit
def test_error_on_missing_required_fields():
    """Test that missing required fields raise errors."""
    from clients.freecad.fctb_parser import parse_fctb_dict, FctbParseError
    
    invalid = {
        "version": 2,
        "name": "Test Tool"
        # Missing shape, shape-type, parameter
    }
    
    with pytest.raises(FctbParseError) as exc:
        parse_fctb_dict(invalid)
    
    assert "required" in str(exc.value).lower()


@pytest.mark.unit
def test_error_on_invalid_json(tmp_path):
    """Test that invalid JSON raises appropriate error."""
    from clients.freecad.fctb_parser import parse_fctb, FctbParseError
    
    invalid_file = tmp_path / "invalid.fctb"
    invalid_file.write_text("{invalid json")
    
    with pytest.raises(FctbParseError) as exc:
        parse_fctb(invalid_file)
    
    assert "json" in str(exc.value).lower()


@pytest.mark.unit
def test_preserve_original_data():
    """Test that original FreeCAD data is preserved for round-trip."""
    from clients.freecad.fctb_parser import parse_fctb_dict
    
    original = {
        "version": 2,
        "id": "test_tool",
        "name": "Test Tool",
        "shape": "test.fcstd",
        "shape-type": "Drill",
        "parameter": {
            "Diameter": "5.00 mm",
            "CustomParam": "CustomValue"
        },
        "attribute": {"custom": "data"}
    }
    
    tool = parse_fctb_dict(original)
    
    # Original data should be accessible
    assert tool["_original"]["version"] == 2
    assert tool["_original"]["parameter"]["CustomParam"] == "CustomValue"
    assert tool["_original"]["attribute"]["custom"] == "data"


@pytest.mark.unit  
def test_all_fixture_files_parse(fixtures_dir):
    """Test that all fixture files parse without errors."""
    from clients.freecad.fctb_parser import parse_fctb
    
    fctb_files = list(fixtures_dir.glob("*.fctb"))
    assert len(fctb_files) > 0, "No fixture files found"
    
    for fctb_file in fctb_files:
        try:
            tool = parse_fctb(fctb_file)
            assert "name" in tool
            assert "shape_type" in tool
            assert "parameters" in tool
        except Exception as e:
            pytest.fail(f"Failed to parse {fctb_file.name}: {e}")
