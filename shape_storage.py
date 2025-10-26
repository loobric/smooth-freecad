# MIT License
# Copyright (c) 2025 sliptonic
# SPDX-License-Identifier: MIT

"""
Tool shape file storage and retrieval utilities.

Handles uploading/downloading tool shape files (FreeCAD .FCStd, STEP, STL, etc.)
to/from the Smooth server.
"""
import hashlib
import json
from pathlib import Path
from typing import Optional, Dict, Any
import base64


def calculate_file_hash(file_path: Path) -> str:
    """Calculate SHA256 hash of a file.
    
    Args:
        file_path: Path to file
        
    Returns:
        Hex string of SHA256 hash
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def prepare_shape_upload(shape_file_path: Path) -> Dict[str, Any]:
    """Prepare shape file data for upload to Smooth.
    
    Args:
        shape_file_path: Path to shape file (.FCStd, .step, etc.)
        
    Returns:
        Dictionary with file data ready for upload
        
    Raises:
        FileNotFoundError: If shape file doesn't exist
    """
    if not shape_file_path.exists():
        raise FileNotFoundError(f"Shape file not found: {shape_file_path}")
    
    # Read file content
    with open(shape_file_path, 'rb') as f:
        file_content = f.read()
    
    # Calculate hash and size
    file_hash = calculate_file_hash(shape_file_path)
    file_size = shape_file_path.stat().st_size
    
    # Get file extension
    file_format = shape_file_path.suffix.lstrip('.').lower()
    
    # Encode content as base64 for JSON transport
    content_b64 = base64.b64encode(file_content).decode('utf-8')
    
    return {
        "filename": shape_file_path.name,
        "format": file_format,
        "content": content_b64,
        "hash": f"sha256:{file_hash}",
        "size_bytes": file_size
    }


def create_shape_data_reference(
    shape_file_path: Optional[Path],
    shape_type: str,
    upload_url: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Create shape_data structure for ToolItem.
    
    Args:
        shape_file_path: Path to shape file (optional)
        shape_type: FreeCAD shape type (Drill, Endmill, etc.)
        upload_url: URL where file was uploaded (optional)
        
    Returns:
        shape_data dictionary or None if no shape file
    """
    if not shape_file_path:
        return None
    
    if not shape_file_path.exists():
        # File doesn't exist locally, just store reference
        return {
            "format": shape_file_path.suffix.lstrip('.').lower(),
            "source_system": "freecad",
            "reference": {
                "type": "local_path",
                "value": str(shape_file_path)
            },
            "metadata": {
                "shape_type": shape_type,
                "original_reference": str(shape_file_path)
            }
        }
    
    # File exists - calculate hash and create full reference
    file_hash = calculate_file_hash(shape_file_path)
    file_size = shape_file_path.stat().st_size
    
    reference_data = {
        "type": "url" if upload_url else "local_path",
        "value": upload_url if upload_url else str(shape_file_path),
        "hash": f"sha256:{file_hash}",
        "size_bytes": file_size
    }
    
    return {
        "format": shape_file_path.suffix.lstrip('.').lower(),
        "source_system": "freecad",
        "reference": reference_data,
        "metadata": {
            "shape_type": shape_type,
            "original_reference": str(shape_file_path),
            "filename": shape_file_path.name
        }
    }


def download_and_save_shape(
    shape_data: Dict[str, Any],
    output_dir: Path,
    filename: Optional[str] = None
) -> Optional[Path]:
    """Download shape file content and save to local directory.
    
    Args:
        shape_data: shape_data dictionary from ToolItem
        output_dir: Directory to save file
        filename: Optional filename (generated if not provided)
        
    Returns:
        Path to saved file or None if no content available
    """
    if not shape_data:
        return None
    
    reference = shape_data.get("reference", {})
    
    # Check if we have inline content
    if reference.get("type") == "inline" and "content" in reference:
        content_b64 = reference["content"]
        file_content = base64.b64decode(content_b64)
    else:
        # No inline content - would need to download from URL
        # For now, return None (URL download not implemented yet)
        return None
    
    # Generate filename if not provided
    if not filename:
        metadata = shape_data.get("metadata", {})
        original_name = metadata.get("filename")
        if original_name:
            filename = original_name
        else:
            file_format = shape_data.get("format", "fcstd")
            filename = f"shape.{file_format}"
    
    # Save file
    output_path = output_dir / filename
    output_dir.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'wb') as f:
        f.write(file_content)
    
    return output_path


def resolve_shape_file_path(
    shape_reference: str,
    base_dir: Path,
    search_dirs: Optional[list[Path]] = None
) -> Optional[Path]:
    """Resolve a shape file path relative to base directories.
    
    Args:
        shape_reference: Shape file reference (e.g., "endmill.fcstd")
        base_dir: Primary base directory
        search_dirs: Additional directories to search
        
    Returns:
        Absolute path to shape file if found, None otherwise
    """
    # Try relative to base_dir first
    candidate = base_dir / shape_reference
    if candidate.exists():
        return candidate
    
    # Try search directories
    if search_dirs:
        for search_dir in search_dirs:
            candidate = search_dir / shape_reference
            if candidate.exists():
                return candidate
    
    return None


def verify_shape_file_integrity(file_path: Path, expected_hash: str) -> bool:
    """Verify shape file integrity using hash.
    
    Args:
        file_path: Path to shape file
        expected_hash: Expected hash in format "sha256:abc123..."
        
    Returns:
        True if hash matches, False otherwise
    """
    if not file_path.exists():
        return False
    
    # Extract hash algorithm and value
    if ':' in expected_hash:
        algo, hash_value = expected_hash.split(':', 1)
    else:
        hash_value = expected_hash
    
    # Calculate actual hash
    actual_hash = calculate_file_hash(file_path)
    
    return actual_hash == hash_value
