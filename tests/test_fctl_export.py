# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tests for FreeCAD tool library (.fctl) export functionality.

Tests the conversion from Smooth format back to FreeCAD library format,
including library generation and tool file writing.
"""
import pytest
from pathlib import Path
from clients.freecad.fctl_parser import (
    smooth_to_fctl,
    write_fctl,
    export_toolset_to_freecad,
    parse_fctl_dict,
    library_to_smooth_tool_set
)


class TestSmoothToFctl:
    """Test Smooth to FreeCAD library conversion."""
    
    def test_basic_conversion_with_members(self):
        """Test basic conversion from ToolSet with members."""
        tool_set = {
            "name": "My Tool Library",
            "members": [
                {"tool_number": 1, "tool_path": "drill_5mm.fctb"},
                {"tool_number": 2, "tool_path": "endmill_6mm.fctb"},
                {"tool_number": 3, "tool_path": "vbit_60deg.fctb"}
            ],
            "freecad_metadata": {
                "version": 1
            }
        }
        
        fctl = smooth_to_fctl(tool_set)
        
        assert fctl["version"] == 1
        assert fctl["label"] == "My Tool Library"
        assert len(fctl["tools"]) == 3
        assert fctl["tools"][0] == {"nr": 1, "path": "drill_5mm.fctb"}
        assert fctl["tools"][1] == {"nr": 2, "path": "endmill_6mm.fctb"}
        assert fctl["tools"][2] == {"nr": 3, "path": "vbit_60deg.fctb"}
    
    def test_conversion_with_tools_list(self):
        """Test conversion from parser format with tools list."""
        tool_set = {
            "name": "Parser Format Library",
            "tools": [
                {"tool_number": 10, "tool_path": "tool_10.fctb"},
                {"tool_number": 20, "tool_path": "tool_20.fctb"}
            ]
        }
        
        fctl = smooth_to_fctl(tool_set)
        
        assert fctl["label"] == "Parser Format Library"
        assert len(fctl["tools"]) == 2
        assert fctl["tools"][0]["nr"] == 10
        assert fctl["tools"][1]["nr"] == 20
    
    def test_conversion_with_tools_dict(self):
        """Test conversion with separate tools dictionary."""
        tool_set = {
            "name": "Dict Format Library"
        }
        
        tools_dict = {
            1: {"type": "cutting_tool", "description": "Tool 1"},
            2: {"type": "cutting_tool", "description": "Tool 2"},
            5: {"type": "probe", "description": "Probe"}
        }
        
        fctl = smooth_to_fctl(tool_set, tools_dict)
        
        assert len(fctl["tools"]) == 3
        assert fctl["tools"][0] == {"nr": 1, "path": "tool_1.fctb"}
        assert fctl["tools"][1] == {"nr": 2, "path": "tool_2.fctb"}
        assert fctl["tools"][2] == {"nr": 5, "path": "tool_5.fctb"}
    
    def test_default_tool_path_generation(self):
        """Test that tool paths are auto-generated when missing."""
        tool_set = {
            "name": "Auto Path Library",
            "members": [
                {"tool_number": 7},  # No tool_path
                {"tool_number": 15}
            ]
        }
        
        fctl = smooth_to_fctl(tool_set)
        
        assert fctl["tools"][0]["path"] == "tool_7.fctb"
        assert fctl["tools"][1]["path"] == "tool_15.fctb"
    
    def test_default_version(self):
        """Test default version when metadata missing."""
        tool_set = {
            "name": "No Metadata Library",
            "members": [{"tool_number": 1, "tool_path": "tool.fctb"}]
        }
        
        fctl = smooth_to_fctl(tool_set)
        
        assert fctl["version"] == 1
    
    def test_unnamed_library(self):
        """Test handling of library without name."""
        tool_set = {
            "members": [{"tool_number": 1, "tool_path": "tool.fctb"}]
        }
        
        fctl = smooth_to_fctl(tool_set)
        
        assert fctl["label"] == "Unnamed Library"


class TestWriteFctl:
    """Test file writing functionality."""
    
    def test_write_fctl(self, tmp_path):
        """Test writing library to file."""
        import json
        
        fctl = {
            "version": 1,
            "label": "Test Library",
            "tools": [
                {"nr": 1, "path": "tool_1.fctb"},
                {"nr": 2, "path": "tool_2.fctb"}
            ]
        }
        
        output_file = tmp_path / "test_library.fctl"
        write_fctl(fctl, output_file)
        
        # Verify file was written
        assert output_file.exists()
        
        # Verify content
        with open(output_file, 'r') as f:
            written = json.load(f)
        
        assert written["label"] == "Test Library"
        assert len(written["tools"]) == 2


class TestExportToolsetToFreecad:
    """Test complete export with tool bit file generation."""
    
    def test_export_toolset_with_tools(self, tmp_path):
        """Test exporting toolset with tool bit files."""
        tool_set = {
            "name": "Export Test Library",
            "members": [
                {"tool_number": 1, "tool_path": "drill_5mm.fctb"},
                {"tool_number": 2, "tool_path": "endmill_6mm.fctb"}
            ]
        }
        
        tools_dict = {
            1: {
                "type": "cutting_tool",
                "description": "5mm Drill",
                "geometry": {
                    "diameter": 5.0,
                    "diameter_unit": "mm",
                    "length": 50.0,
                    "length_unit": "mm"
                },
                "material": {"type": "HSS"},
                "freecad_metadata": {
                    "shape": "drill.fcstd",
                    "shape_type": "Drill"
                }
            },
            2: {
                "type": "cutting_tool",
                "description": "6mm Endmill",
                "geometry": {
                    "diameter": 6.0,
                    "diameter_unit": "mm",
                    "length": 60.0,
                    "length_unit": "mm"
                },
                "material": {"type": "Carbide"},
                "freecad_metadata": {
                    "shape": "endmill.fcstd",
                    "shape_type": "Endmill"
                }
            }
        }
        
        output_dir = tmp_path / "exported_library"
        library_path = export_toolset_to_freecad(tool_set, tools_dict, output_dir)
        
        # Verify library file was created
        assert library_path.exists()
        assert library_path.name == "export_test_library.fctl"
        
        # Verify tool bit files were created
        assert (output_dir / "drill_5mm.fctb").exists()
        assert (output_dir / "endmill_6mm.fctb").exists()
        
        # Verify library content
        import json
        with open(library_path, 'r') as f:
            library = json.load(f)
        
        assert library["label"] == "Export Test Library"
        assert len(library["tools"]) == 2
        
        # Verify tool bit content
        with open(output_dir / "drill_5mm.fctb", 'r') as f:
            drill = json.load(f)
        
        assert drill["name"] == "5mm Drill"
        assert drill["shape-type"] == "Drill"
        assert drill["parameter"]["Diameter"] == "5.00 mm"
    
    def test_export_raises_on_missing_tool(self, tmp_path):
        """Test that export raises error when tool data is missing."""
        tool_set = {
            "name": "Incomplete Library",
            "members": [
                {"tool_number": 1, "tool_path": "tool_1.fctb"},
                {"tool_number": 2, "tool_path": "tool_2.fctb"}
            ]
        }
        
        tools_dict = {
            1: {
                "type": "cutting_tool",
                "description": "Tool 1",
                "geometry": {"diameter": 5.0, "diameter_unit": "mm"}
            }
            # Missing tool 2
        }
        
        with pytest.raises(ValueError) as excinfo:
            export_toolset_to_freecad(tool_set, tools_dict, tmp_path)
        
        assert "Tool number 2" in str(excinfo.value)
        assert "not found" in str(excinfo.value)


class TestRoundTrip:
    """Test round-trip conversion for tool libraries."""
    
    def test_library_round_trip(self, tmp_path):
        """Test round-trip: FreeCAD -> Smooth -> FreeCAD."""
        from clients.freecad.fctb_parser import parse_fctb_dict, fctb_to_smooth
        
        # Create original library
        original_library = {
            "version": 1,
            "label": "Round Trip Test",
            "tools": [
                {"nr": 1, "path": "drill_5mm.fctb"},
                {"nr": 2, "path": "endmill_6mm.fctb"}
            ]
        }
        
        # Create tool bit files
        tool_bits = {
            1: {
                "version": 2,
                "name": "5mm Drill",
                "shape": "drill.fcstd",
                "shape-type": "Drill",
                "parameter": {
                    "Diameter": "5.00 mm",
                    "Length": "50.00 mm",
                    "Material": "HSS"
                },
                "attribute": {}
            },
            2: {
                "version": 2,
                "name": "6mm Endmill",
                "shape": "endmill.fcstd",
                "shape-type": "Endmill",
                "parameter": {
                    "Diameter": "6.00 mm",
                    "Length": "60.00 mm",
                    "Flutes": 4,
                    "Material": "Carbide"
                },
                "attribute": {}
            }
        }
        
        # Write tool bit files to temp directory
        bits_dir = tmp_path / "bits"
        bits_dir.mkdir()
        
        import json
        for nr, tool_data in tool_bits.items():
            tool_path = original_library["tools"][nr - 1]["path"]
            with open(bits_dir / tool_path, 'w') as f:
                json.dump(tool_data, f)
        
        # Parse to Smooth format
        parsed_library = parse_fctl_dict(original_library)
        smooth_tool_set = library_to_smooth_tool_set(parsed_library, bits_dir)
        
        # Convert tool bits to Smooth format
        smooth_tools = {}
        for tool_item in smooth_tool_set["tools"]:
            if "tool_data" in tool_item:
                parsed_tool = parse_fctb_dict(tool_item["tool_data"]["_original"])
                smooth_tool = fctb_to_smooth(parsed_tool)
                smooth_tools[tool_item["tool_number"]] = smooth_tool
        
        # Convert back to FreeCAD format
        exported_dir = tmp_path / "exported"
        library_path = export_toolset_to_freecad(smooth_tool_set, smooth_tools, exported_dir)
        
        # Verify exported library matches original structure
        with open(library_path, 'r') as f:
            exported_library = json.load(f)
        
        assert exported_library["version"] == original_library["version"]
        assert exported_library["label"] == original_library["label"]
        assert len(exported_library["tools"]) == len(original_library["tools"])
        
        # Verify tool entries match
        for orig_tool, exp_tool in zip(original_library["tools"], exported_library["tools"]):
            assert exp_tool["nr"] == orig_tool["nr"]
            # Path might differ but should reference a valid file
            assert (exported_dir / exp_tool["path"]).exists()
