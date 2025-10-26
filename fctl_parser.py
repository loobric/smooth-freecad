# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
FreeCAD tool library (.fctl) file parser.

Parses FreeCAD tool library JSON files and converts them to Smooth's format.

Assumptions:
- .fctl files are valid JSON
- Libraries reference tool bits by relative path
- Tool numbers (nr) assign position in tool table
- Libraries can map to ToolSet or ToolPreset collections
"""
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from collections import Counter

# Support both FreeCAD addon context (direct import) and pytest context (package import)
try:
    from fctb_parser import parse_fctb, FctbParseError
except ModuleNotFoundError:
    from clients.freecad.fctb_parser import parse_fctb, FctbParseError


class FctlParseError(Exception):
    """Error parsing FreeCAD tool library file."""
    pass


def parse_fctl_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse FreeCAD tool library data from dictionary.
    
    Args:
        data: Parsed JSON data from .fctl file
        
    Returns:
        Normalized tool library dictionary
        
    Raises:
        FctlParseError: If required fields are missing
    """
    # Validate required fields (only version and tools are truly required)
    required_fields = ["version", "tools"]
    missing = [field for field in required_fields if field not in data]
    if missing:
        raise FctlParseError(f"Missing required fields: {', '.join(missing)}")
    
    # Validate tools is a list
    if not isinstance(data["tools"], list):
        raise FctlParseError("'tools' field must be a list")
    
    # Get label from either 'label' or 'name' field, or None if neither exists
    label = data.get("label") or data.get("name")
    
    # Build normalized structure
    library = {
        "version": data["version"],
        "label": label,  # May be None
        "tools": data["tools"],
        "_original": data  # Preserve original for round-trip
    }
    
    return library


def parse_fctl(file_path: Path) -> Dict[str, Any]:
    """Parse FreeCAD tool library file.
    
    Args:
        file_path: Path to .fctl file
        
    Returns:
        Parsed tool library dictionary
        
    Raises:
        FctlParseError: If file cannot be parsed
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise FctlParseError(f"Invalid JSON in {file_path}: {e}")
    except Exception as e:
        raise FctlParseError(f"Error reading {file_path}: {e}")
    
    return parse_fctl_dict(data)


def resolve_tool_path(tool_path: str, bits_dir: Path) -> Path:
    """Resolve tool bit path relative to bits directory.
    
    Args:
        tool_path: Relative path from library (e.g., "drill_5.0mm.fctb")
        bits_dir: Base directory containing tool bit files
        
    Returns:
        Absolute path to tool bit file
    """
    return bits_dir / tool_path


def load_library_with_tools(library: Dict[str, Any], bits_dir: Path) -> Dict[str, Any]:
    """Load library and resolve all tool bit references.
    
    Args:
        library: Parsed library dictionary from parse_fctl()
        bits_dir: Directory containing tool bit files
        
    Returns:
        Library with resolved tool data
        
    Note:
        Tools that cannot be loaded are included but marked as unresolved.
    """
    loaded_library = library.copy()
    loaded_tools = []
    
    for tool_entry in library["tools"]:
        loaded_entry = tool_entry.copy()
        
        # Try to load the tool bit file
        tool_path = resolve_tool_path(tool_entry["path"], bits_dir)
        
        if tool_path.exists():
            try:
                tool_data = parse_fctb(tool_path)
                loaded_entry["tool_data"] = tool_data
                loaded_entry["resolved"] = True
            except FctbParseError as e:
                loaded_entry["error"] = str(e)
                loaded_entry["resolved"] = False
        else:
            loaded_entry["resolved"] = False
        
        loaded_tools.append(loaded_entry)
    
    loaded_library["tools"] = loaded_tools
    return loaded_library


def library_to_smooth_tool_set(library: Dict[str, Any], bits_dir: Path) -> Dict[str, Any]:
    """Convert FreeCAD library to Smooth ToolSet format.
    
    Args:
        library: Parsed library dictionary
        bits_dir: Directory containing tool bit files
        
    Returns:
        Dictionary in Smooth ToolSet format
        
    Note:
        ToolSet is a collection of tools without machine-specific info.
    """
    loaded = load_library_with_tools(library, bits_dir)
    
    tools = []
    for tool_entry in loaded["tools"]:
        tool_item = {
            "tool_number": tool_entry["nr"],
            "tool_path": tool_entry["path"]
        }
        
        if tool_entry.get("resolved") and "tool_data" in tool_entry:
            tool_item["tool_data"] = tool_entry["tool_data"]
        
        tools.append(tool_item)
    
    tool_set = {
        "name": library["label"],
        "description": f"FreeCAD tool library: {library['label']}",
        "tools": tools,
        "freecad_metadata": {
            "version": library["version"],
            "original_label": library["label"]
        }
    }
    
    return tool_set


def library_to_smooth_presets(
    library: Dict[str, Any],
    bits_dir: Path,
    machine_id: str
) -> List[Dict[str, Any]]:
    """Convert FreeCAD library to Smooth ToolPreset collection.
    
    Args:
        library: Parsed library dictionary
        bits_dir: Directory containing tool bit files
        machine_id: Machine identifier for presets
        
    Returns:
        List of ToolPreset dictionaries
        
    Note:
        Each tool in library becomes a ToolPreset with machine_id and tool_number.
    """
    loaded = load_library_with_tools(library, bits_dir)
    
    presets = []
    for tool_entry in loaded["tools"]:
        if not tool_entry.get("resolved") or "tool_data" not in tool_entry:
            # Skip unresolved tools
            continue
        
        preset = {
            "machine_id": machine_id,
            "tool_number": tool_entry["nr"],
            "tool_data": tool_entry["tool_data"],
            "source": "freecad",
            "source_path": tool_entry["path"]
        }
        
        presets.append(preset)
    
    return presets


def check_tool_number_uniqueness(library: Dict[str, Any]) -> List[int]:
    """Check for duplicate tool numbers in library.
    
    Args:
        library: Parsed library dictionary
        
    Returns:
        List of duplicate tool numbers (empty if all unique)
    """
    tool_numbers = [tool["nr"] for tool in library["tools"]]
    counter = Counter(tool_numbers)
    duplicates = [nr for nr, count in counter.items() if count > 1]
    return duplicates


def get_tool_by_number(library: Dict[str, Any], tool_number: int) -> Optional[Dict[str, Any]]:
    """Find a tool in library by its tool number.
    
    Args:
        library: Parsed library dictionary
        tool_number: Tool number to find
        
    Returns:
        Tool entry dictionary if found, None otherwise
    """
    for tool in library["tools"]:
        if tool["nr"] == tool_number:
            return tool
    return None


def smooth_to_fctl(
    tool_set: Dict[str, Any],
    tools_dict: Optional[Dict[int, Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Convert Smooth ToolSet to FreeCAD library format.
    
    Args:
        tool_set: Smooth ToolSet or collection of tools with tool_numbers
        tools_dict: Optional mapping of tool_number -> tool data (Smooth format)
                   If not provided, assumes tool data is in tool_set['members']
        
    Returns:
        Dictionary in FreeCAD .fctl format
        
    Assumptions:
    - tools_dict contains Smooth-format tool data that can be exported to .fctb
    - Tool numbers are unique
    - Tool paths will be generated as "tool_{nr}.fctb"
    """
    metadata = tool_set.get("freecad_metadata") or {}
    
    # Get tools from either members list or tools_dict
    tools = []
    
    # Handle ToolSet.members format
    if "members" in tool_set:
        for member in tool_set["members"]:
            tool_entry = {
                "nr": member.get("tool_number"),
                "path": member.get("tool_path") or f"tool_{member.get('tool_number')}.fctb"
            }
            tools.append(tool_entry)
    # Handle direct tools list (from parser)
    elif "tools" in tool_set:
        for tool_item in tool_set["tools"]:
            tool_entry = {
                "nr": tool_item.get("tool_number"),
                "path": tool_item.get("tool_path") or f"tool_{tool_item.get('tool_number')}.fctb"
            }
            tools.append(tool_entry)
    # Handle tools_dict mapping
    elif tools_dict:
        for tool_number, tool_data in sorted(tools_dict.items()):
            tool_entry = {
                "nr": tool_number,
                "path": f"tool_{tool_number}.fctb"
            }
            tools.append(tool_entry)
    
    # Build FreeCAD library structure
    fctl = {
        "version": metadata.get("version", 1),
        "label": tool_set.get("name") or "Unnamed Library",
        "tools": tools
    }
    
    return fctl


def write_fctl(library: Dict[str, Any], file_path: Path) -> None:
    """Write FreeCAD tool library to file.
    
    Args:
        library: Tool library dictionary in FreeCAD format
        file_path: Path to write .fctl file
        
    Raises:
        IOError: If file cannot be written
    """
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(library, f, indent=2, ensure_ascii=False)
    except Exception as e:
        raise IOError(f"Error writing {file_path}: {e}")


def export_toolset_to_freecad(
    tool_set: Dict[str, Any],
    tools_dict: Dict[int, Dict[str, Any]],
    output_dir: Path
) -> Path:
    """Export a Smooth ToolSet to FreeCAD library and tool bit files.
    
    Args:
        tool_set: Smooth ToolSet dictionary
        tools_dict: Mapping of tool_number -> Smooth tool data
        output_dir: Directory to write library and tool files
        
    Returns:
        Path to created .fctl library file
        
    Raises:
        ValueError: If tool data is missing or invalid
        IOError: If files cannot be written
        
    Note:
        Creates a .fctl library file and individual .fctb files for each tool.
    """
    try:
        from fctb_parser import smooth_to_fctb, write_fctb
    except ModuleNotFoundError:
        from clients.freecad.fctb_parser import smooth_to_fctb, write_fctb
    
    # Create output directory if needed
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate library
    library = smooth_to_fctl(tool_set, tools_dict)
    
    # Write individual tool bit files
    for tool_entry in library["tools"]:
        tool_number = tool_entry["nr"]
        tool_path = tool_entry["path"]
        
        if tool_number not in tools_dict:
            raise ValueError(f"Tool number {tool_number} not found in tools_dict")
        
        # Convert to FreeCAD format and write
        tool_data = tools_dict[tool_number]
        fctb = smooth_to_fctb(tool_data)
        
        tool_file_path = output_dir / tool_path
        write_fctb(fctb, tool_file_path)
    
    # Write library file
    library_name = tool_set.get("name", "library").replace(" ", "_").lower()
    library_path = output_dir / f"{library_name}.fctl"
    write_fctl(library, library_path)
    
    return library_path
