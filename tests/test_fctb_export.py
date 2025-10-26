# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for FreeCAD tool bit (.fctb) export functionality.

Tests the conversion from Smooth format back to FreeCAD format,
including unit handling, parameter conversion, and round-trip validation.
"""
import pytest
from pathlib import Path
from clients.freecad.fctb_parser import (
    smooth_to_fctb,
    write_fctb,
    snake_to_camel,
    format_parameter_value,
    reverse_map_shape_type
)


class TestParameterConversion:
    """Test helper functions for parameter conversion."""
    
    def test_snake_to_camel(self):
        """Test snake_case to CamelCase conversion."""
        assert snake_to_camel("tip_angle") == "TipAngle"
        assert snake_to_camel("cutting_edge_height") == "CuttingEdgeHeight"
        assert snake_to_camel("diameter") == "Diameter"
        assert snake_to_camel("shank_diameter") == "ShankDiameter"
    
    def test_format_parameter_value_with_unit(self):
        """Test formatting parameter values with units."""
        assert format_parameter_value(5.0, "mm") == "5.00 mm"
        assert format_parameter_value(60.0, "°") == "60.00°"
        assert format_parameter_value(3.5, "in") == "3.50 in"
        assert format_parameter_value(119, "°") == "119.00°"
    
    def test_format_parameter_value_without_unit(self):
        """Test formatting non-dimensional values."""
        assert format_parameter_value("HSS", None) == "HSS"
        assert format_parameter_value("Carbide", None) == "Carbide"
        assert format_parameter_value(4, None) == "4"
    
    def test_reverse_map_shape_type(self):
        """Test reverse mapping of shape types."""
        assert reverse_map_shape_type("cutting_tool") == "Endmill"
        assert reverse_map_shape_type("probe") == "Probe"
        assert reverse_map_shape_type("unknown") == "Endmill"


class TestSmoothToFctb:
    """Test Smooth to FreeCAD conversion."""
    
    def test_basic_conversion(self):
        """Test basic conversion with units."""
        smooth_tool = {
            "type": "cutting_tool",
            "description": "6mm Endmill",
            "geometry": {
                "diameter": 6.0,
                "diameter_unit": "mm",
                "length": 50.0,
                "length_unit": "mm",
                "flutes": 4
            },
            "material": {
                "type": "HSS"
            },
            "freecad_metadata": {
                "shape": "endmill.fcstd",
                "shape_type": "Endmill",
                "version": 2
            }
        }
        
        fctb = smooth_to_fctb(smooth_tool)
        
        assert fctb["version"] == 2
        assert fctb["name"] == "6mm Endmill"
        assert fctb["shape"] == "endmill.fcstd"
        assert fctb["shape-type"] == "Endmill"
        assert fctb["parameter"]["Diameter"] == "6.00 mm"
        assert fctb["parameter"]["Length"] == "50.00 mm"
        assert fctb["parameter"]["Flutes"] == 4
        assert fctb["parameter"]["Material"] == "HSS"
    
    def test_imperial_units(self):
        """Test conversion with imperial units."""
        smooth_tool = {
            "type": "cutting_tool",
            "description": "1/4 inch drill",
            "geometry": {
                "diameter": 0.25,
                "diameter_unit": "in",
                "length": 2.5,
                "length_unit": "in"
            },
            "material": {
                "type": "Carbide"
            }
        }
        
        fctb = smooth_to_fctb(smooth_tool)
        
        assert fctb["parameter"]["Diameter"] == "0.25 in"
        assert fctb["parameter"]["Length"] == "2.50 in"
    
    def test_missing_unit_raises_error(self):
        """Test that missing units for dimensional parameters raises error."""
        smooth_tool = {
            "type": "cutting_tool",
            "description": "Tool with missing unit",
            "geometry": {
                "diameter": 5.0,
                # Missing diameter_unit
            }
        }
        
        with pytest.raises(ValueError) as excinfo:
            smooth_to_fctb(smooth_tool)
        
        assert "diameter" in str(excinfo.value)
        assert "unit" in str(excinfo.value).lower()
    
    def test_drill_with_tip_angle(self):
        """Test drill conversion with tip angle."""
        smooth_tool = {
            "type": "cutting_tool",
            "description": "118° Drill",
            "geometry": {
                "diameter": 8.0,
                "diameter_unit": "mm",
                "length": 75.0,
                "length_unit": "mm",
                "tip_angle": 118.0,
                "tip_angle_unit": "°"
            },
            "material": {
                "type": "HSS"
            },
            "freecad_metadata": {
                "shape": "drill.fcstd",
                "shape_type": "Drill",
                "version": 2
            }
        }
        
        fctb = smooth_to_fctb(smooth_tool)
        
        assert fctb["shape-type"] == "Drill"
        assert fctb["parameter"]["TipAngle"] == "118.00°"
    
    def test_vbit_with_cutting_edge_angle(self):
        """Test V-bit conversion with cutting edge angle."""
        smooth_tool = {
            "type": "cutting_tool",
            "description": "60° V-Bit",
            "geometry": {
                "diameter": 12.7,
                "diameter_unit": "mm",
                "cutting_edge_angle": 60.0,
                "cutting_edge_angle_unit": "°",
                "cutting_edge_height": 10.0,
                "cutting_edge_height_unit": "mm"
            },
            "material": {
                "type": "Carbide"
            },
            "freecad_metadata": {
                "shape": "vbit.fcstd",
                "shape_type": "VBit",
                "version": 2
            }
        }
        
        fctb = smooth_to_fctb(smooth_tool)
        
        assert fctb["shape-type"] == "VBit"
        assert fctb["parameter"]["CuttingEdgeAngle"] == "60.00°"
        assert fctb["parameter"]["CuttingEdgeHeight"] == "10.00 mm"
    
    def test_probe_type(self):
        """Test probe tool conversion."""
        smooth_tool = {
            "type": "probe",
            "description": "Touch Probe",
            "geometry": {
                "diameter": 3.0,
                "diameter_unit": "mm",
                "length": 50.0,
                "length_unit": "mm"
            },
            "freecad_metadata": {
                "shape": "probe.fcstd",
                "shape_type": "Probe",
                "version": 2
            }
        }
        
        fctb = smooth_to_fctb(smooth_tool)
        
        assert fctb["shape-type"] == "Probe"
    
    def test_default_shape(self):
        """Test default shape when metadata missing."""
        smooth_tool = {
            "type": "cutting_tool",
            "description": "Basic Tool",
            "geometry": {
                "diameter": 6.0,
                "diameter_unit": "mm"
            }
        }
        
        fctb = smooth_to_fctb(smooth_tool, default_shape="default_shape")
        
        assert fctb["shape"] == "default_shape.fcstd"
        assert fctb["shape-type"] == "Endmill"
    
    def test_id_preservation(self):
        """Test that id is preserved from metadata or tool."""
        smooth_tool1 = {
            "type": "cutting_tool",
            "description": "Tool with metadata id",
            "geometry": {"diameter": 6.0, "diameter_unit": "mm"},
            "freecad_metadata": {"id": "metadata-id-123"}
        }
        
        smooth_tool2 = {
            "id": "tool-id-456",
            "type": "cutting_tool",
            "description": "Tool with direct id",
            "geometry": {"diameter": 6.0, "diameter_unit": "mm"}
        }
        
        fctb1 = smooth_to_fctb(smooth_tool1)
        fctb2 = smooth_to_fctb(smooth_tool2)
        
        assert fctb1["id"] == "metadata-id-123"
        assert fctb2["id"] == "tool-id-456"


class TestRoundTrip:
    """Test round-trip conversion from FreeCAD -> Smooth -> FreeCAD."""
    
    def test_endmill_round_trip(self):
        """Test round-trip for endmill preserves data."""
        from clients.freecad.fctb_parser import parse_fctb_dict, fctb_to_smooth
        
        original = {
            "version": 2,
            "id": "endmill-6mm",
            "name": "6mm 4-Flute Endmill",
            "shape": "endmill.fcstd",
            "shape-type": "Endmill",
            "parameter": {
                "Diameter": "6.00 mm",
                "Length": "50.00 mm",
                "Flutes": 4,
                "Material": "HSS",
                "ShankDiameter": "6.00 mm"
            },
            "attribute": {}
        }
        
        # Parse to Smooth format
        parsed = parse_fctb_dict(original)
        smooth = fctb_to_smooth(parsed)
        
        # Convert back to FreeCAD format
        exported = smooth_to_fctb(smooth)
        
        # Verify key fields match
        assert exported["version"] == original["version"]
        assert exported["name"] == original["name"]
        assert exported["shape"] == original["shape"]
        assert exported["shape-type"] == original["shape-type"]
        assert exported["parameter"]["Diameter"] == original["parameter"]["Diameter"]
        assert exported["parameter"]["Length"] == original["parameter"]["Length"]
        assert exported["parameter"]["Flutes"] == original["parameter"]["Flutes"]
        assert exported["parameter"]["Material"] == original["parameter"]["Material"]


class TestWriteFctb:
    """Test file writing functionality."""
    
    def test_write_fctb(self, tmp_path):
        """Test writing tool bit to file."""
        import json
        
        fctb = {
            "version": 2,
            "name": "Test Tool",
            "shape": "endmill.fcstd",
            "shape-type": "Endmill",
            "parameter": {
                "Diameter": "6.00 mm"
            },
            "attribute": {}
        }
        
        output_file = tmp_path / "test_tool.fctb"
        write_fctb(fctb, output_file)
        
        # Verify file was written
        assert output_file.exists()
        
        # Verify content
        with open(output_file, 'r') as f:
            written = json.load(f)
        
        assert written["name"] == "Test Tool"
        assert written["parameter"]["Diameter"] == "6.00 mm"
