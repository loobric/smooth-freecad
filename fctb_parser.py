# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
FreeCAD tool bit (.fctb) file parser.

Parses FreeCAD tool bit JSON files and converts them to Smooth's tool data format.

Assumptions:
- .fctb files are valid JSON
- Parameter values may include units as strings (e.g., "5.00 mm", "60.00°")
- Different shape types have different parameter sets
- Preserve original data for round-trip conversion
"""
import json
import re
from pathlib import Path
from typing import Dict, Any, Union


class FctbParseError(Exception):
    """Error parsing FreeCAD tool bit file."""
    pass


def parse_parameter_value(value: Any) -> Union[Dict[str, Any], str, int, float]:
    """Parse a parameter value that may include units.
    
    Args:
        value: Parameter value (may be string with units, number, or plain string)
        
    Returns:
        Dict with 'value' and 'unit' keys if units found, otherwise original value
        
    Examples:
        "5.00 mm" -> {"value": 5.0, "unit": "mm"}
        "119.00°" -> {"value": 119.0, "unit": "°"}
        "HSS" -> "HSS"
        2 -> 2
    """
    # If not a string, return as-is
    if not isinstance(value, str):
        return value
    
    # Try to parse numeric value with unit
    # Pattern: number (with optional decimal) followed by optional space and unit
    match = re.match(r'^([-+]?[0-9]*\.?[0-9]+)\s*(.+)$', value.strip())
    
    if match:
        numeric_part = match.group(1)
        unit_part = match.group(2)
        
        # Check if unit part looks like a unit (mm, °, etc.)
        # If it's just text without typical unit characters, treat as string
        if unit_part and any(c in unit_part for c in ['m', '°', 'in', 'deg']):
            return {
                "value": float(numeric_part),
                "unit": unit_part.strip()
            }
    
    # Return as plain string if no unit pattern found
    return value


def parse_fctb_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse FreeCAD tool bit data from dictionary.
    
    Args:
        data: Parsed JSON data from .fctb file
        
    Returns:
        Normalized tool bit dictionary with parsed parameters
        
    Raises:
        FctbParseError: If required fields are missing
    """
    # Validate required fields
    required_fields = ["version", "name", "shape", "shape-type", "parameter"]
    missing = [field for field in required_fields if field not in data]
    if missing:
        raise FctbParseError(f"Missing required fields: {', '.join(missing)}")
    
    # Parse parameters with units
    parsed_parameters = {}
    for key, value in data["parameter"].items():
        parsed_parameters[key] = parse_parameter_value(value)
    
    # Build normalized structure
    tool = {
        "version": data["version"],
        "id": data.get("id"),  # Optional in some files
        "name": data["name"],
        "shape": data["shape"],
        "shape_type": data["shape-type"],
        "parameters": parsed_parameters,
        "attribute": data.get("attribute", {}),
        "_original": data  # Preserve original for round-trip
    }
    
    return tool


def parse_fctb(file_path: Path) -> Dict[str, Any]:
    """Parse FreeCAD tool bit file.
    
    Args:
        file_path: Path to .fctb file
        
    Returns:
        Parsed tool bit dictionary
        
    Raises:
        FctbParseError: If file cannot be parsed
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise FctbParseError(f"Invalid JSON in {file_path}: {e}")
    except Exception as e:
        raise FctbParseError(f"Error reading {file_path}: {e}")
    
    return parse_fctb_dict(data)


def map_shape_type(freecad_shape_type: str) -> str:
    """Map FreeCAD shape type to Smooth tool type.
    
    Args:
        freecad_shape_type: FreeCAD shape-type value
        
    Returns:
        Smooth tool type string
        
    Note:
        Currently maps most cutting tools to "cutting_tool".
        May need more granular mapping based on requirements.
    """
    # Map FreeCAD shape types to Smooth types
    shape_type_map = {
        "Drill": "cutting_tool",
        "Endmill": "cutting_tool",
        "Ballend": "cutting_tool",
        "VBit": "cutting_tool",
        "ThreadMill": "cutting_tool",
        "ChamferMill": "cutting_tool",
        "CornerRound": "cutting_tool",
        "Reamer": "cutting_tool",
        "SpottingDrill": "cutting_tool",
        "SurfacingBit": "cutting_tool",
        "Probe": "probe",
    }
    
    return shape_type_map.get(freecad_shape_type, "cutting_tool")


def fctb_to_smooth(tool: Dict[str, Any]) -> Dict[str, Any]:
    """Convert parsed FreeCAD tool bit to Smooth ToolItem format.
    
    Args:
        tool: Parsed tool bit dictionary from parse_fctb()
        
    Returns:
        Dictionary in Smooth ToolItem format
        
    Assumptions:
    - Diameter is extracted to geometry.diameter
    - Material is extracted to material.type
    - All other parameters go to geometry dict
    - Original FreeCAD data preserved in metadata
    """
    params = tool["parameters"]
    
    # Extract geometry parameters
    geometry = {}
    for key, value in params.items():
        if key == "Material":
            continue  # Handle separately
        
        # Convert parameter name to snake_case
        param_key = key[0].lower() + key[1:]
        param_key = re.sub(r'(?<!^)(?=[A-Z])', '_', param_key).lower()
        
        if isinstance(value, dict) and "value" in value:
            # Store value with unit info
            geometry[param_key] = value["value"]
            geometry[f"{param_key}_unit"] = value["unit"]
        else:
            geometry[param_key] = value
    
    # Extract material
    material = {}
    if "Material" in params:
        material["type"] = params["Material"]
    
    # Extract shape file reference
    shape_data = None
    if tool.get("shape"):
        shape_data = {
            "format": "fcstd",
            "source_system": "freecad",
            "reference": {
                "type": "local_path",
                "value": tool["shape"]
            },
            "metadata": {
                "shape_type": tool["shape_type"],
                "original_reference": tool["shape"]
            }
        }
    
    # Build Smooth format
    smooth_tool = {
        "type": map_shape_type(tool["shape_type"]),
        "description": tool["name"],
        "geometry": geometry,
        "material": material if material else None,
        "shape_data": shape_data,
        "freecad_metadata": {
            "shape": tool["shape"],
            "shape_type": tool["shape_type"],
            "id": tool.get("id"),
            "version": tool["version"]
        }
    }
    
    return smooth_tool


def reverse_map_shape_type(smooth_type: str) -> str:
    """Map Smooth tool type back to FreeCAD shape type.
    
    Args:
        smooth_type: Smooth tool type string
        
    Returns:
        FreeCAD shape-type string
        
    Note:
        Defaults to "Endmill" if no specific mapping found.
        Uses freecad_metadata.shape_type if available.
    """
    # Default reverse mapping - prefer specific types from metadata
    type_map = {
        "probe": "Probe",
        "cutting_tool": "Endmill"  # Default for cutting tools
    }
    
    return type_map.get(smooth_type, "Endmill")


def snake_to_camel(snake_str: str) -> str:
    """Convert snake_case to CamelCase.
    
    Args:
        snake_str: String in snake_case format
        
    Returns:
        String in CamelCase format
        
    Examples:
        "tip_angle" -> "TipAngle"
        "cutting_edge_height" -> "CuttingEdgeHeight"
    """
    components = snake_str.split('_')
    return ''.join(x.title() for x in components)


def format_parameter_value(value: Any, unit: str = None) -> str:
    """Format a parameter value with unit for FreeCAD.
    
    Args:
        value: Numeric value or string
        unit: Unit string (e.g., "mm", "°", "in")
        
    Returns:
        Formatted string as FreeCAD expects
        
    Examples:
        (5.0, "mm") -> "5.00 mm"
        (60.0, "°") -> "60.00°"
        ("HSS", None) -> "HSS"
    """
    if unit is None:
        return str(value)
    
    # Format numeric values with 2 decimal places
    if isinstance(value, (int, float)):
        # Degree symbol has no space between value and unit
        if unit == "°":
            return f"{value:.2f}°"
        return f"{value:.2f} {unit}"
    
    return str(value)


def smooth_to_fctb(smooth_tool: Dict[str, Any], default_shape: str = "endmill") -> Dict[str, Any]:
    """Convert Smooth ToolItem format to FreeCAD tool bit format.
    
    Args:
        smooth_tool: Dictionary in Smooth ToolItem format
        default_shape: Default shape file reference if not in metadata
        
    Returns:
        Dictionary in FreeCAD .fctb format
        
    Raises:
        ValueError: If required unit information is missing
        
    Assumptions:
    - geometry fields with dimensional values must have corresponding _unit fields
    - Material type comes from material.type
    - Uses freecad_metadata if available for shape info
    - Generates id if not present
    """
    geometry = smooth_tool.get("geometry", {})
    material = smooth_tool.get("material") or {}
    metadata = smooth_tool.get("freecad_metadata") or {}
    shape_data = smooth_tool.get("shape_data") or {}
    
    # Build FreeCAD parameters
    parameters = {}
    
    # Process geometry fields
    processed_keys = set()
    for key, value in geometry.items():
        if key.endswith('_unit'):
            continue  # Skip unit keys, process with value
        
        if key in processed_keys:
            continue
            
        # Check if this is a dimensional parameter with units
        unit_key = f"{key}_unit"
        if unit_key in geometry:
            unit = geometry[unit_key]
            param_name = snake_to_camel(key)
            parameters[param_name] = format_parameter_value(value, unit)
            processed_keys.add(key)
            processed_keys.add(unit_key)
        else:
            # Non-dimensional parameter (e.g., flutes count)
            param_name = snake_to_camel(key)
            if isinstance(value, (int, float)):
                # Check if this might be a dimensional value without units
                # For safety, store as-is but warn about missing units
                if key in ['diameter', 'length', 'height', 'angle', 'tip_angle', 
                          'cutting_edge_height', 'shank_diameter']:
                    raise ValueError(
                        f"Dimensional parameter '{key}' missing required unit field '{unit_key}'"
                    )
                parameters[param_name] = value
            else:
                parameters[param_name] = str(value)
            processed_keys.add(key)
    
    # Add material if present
    if material.get("type"):
        parameters["Material"] = material["type"]
    
    # Determine shape-type
    if "shape_type" in metadata:
        shape_type = metadata["shape_type"]
    else:
        shape_type = reverse_map_shape_type(smooth_tool.get("type", "cutting_tool"))
    
    # Determine shape file reference
    shape_file = metadata.get("shape")
    if not shape_file and shape_data:
        # Try to get shape from shape_data
        shape_ref = shape_data.get("reference", {})
        if shape_ref.get("value"):
            shape_file = shape_ref["value"]
        elif shape_data.get("metadata", {}).get("original_reference"):
            shape_file = shape_data["metadata"]["original_reference"]
    
    if not shape_file:
        shape_file = f"{default_shape}.fcstd"
    
    # Build FreeCAD structure
    fctb = {
        "version": metadata.get("version", 2),
        "name": smooth_tool.get("description", "Unnamed Tool"),
        "shape": shape_file,
        "shape-type": shape_type,
        "parameter": parameters,
        "attribute": {}
    }
    
    # Add id if available
    if "id" in metadata:
        fctb["id"] = metadata["id"]
    elif "id" in smooth_tool:
        fctb["id"] = smooth_tool["id"]
    
    return fctb


def write_fctb(tool: Dict[str, Any], file_path: Path) -> None:
    """Write FreeCAD tool bit to file.
    
    Args:
        tool: Tool bit dictionary in FreeCAD format
        file_path: Path to write .fctb file
        
    Raises:
        IOError: If file cannot be written
    """
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(tool, f, indent=2, ensure_ascii=False)
    except Exception as e:
        raise IOError(f"Error writing {file_path}: {e}")
