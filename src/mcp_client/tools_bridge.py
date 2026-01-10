"""
MCP Tools to Gemini Function Declarations Bridge.

This module provides utilities to convert MCP tool definitions into
Google Gemini (Geneative AI) compatible function declarations.
"""

import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

def convert_mcp_tool_to_gemini(mcp_tool: Any) -> Dict[str, Any]:
    """
    Convert a single MCP Tool object to a Gemini FunctionDeclaration dict.
    
    Args:
        mcp_tool: mcp.types.Tool object or dict
        
    Returns:
        Dict representing Gemini FunctionDeclaration
    """
    # Handle both object and dict (just in case)
    if isinstance(mcp_tool, dict):
        name = mcp_tool.get("name")
        description = mcp_tool.get("description")
        input_schema = mcp_tool.get("inputSchema", {})
    else:
        name = mcp_tool.name
        description = mcp_tool.description
        input_schema = mcp_tool.inputSchema
        
    # Gemini requires parameters to be a Schema object.
    # MCP inputSchema is already a JSON Schema object, which maps well to Gemini.
    # However, Gemini SDK/API rejects '$schema' and strict validation might fail on other fields.
    
    # Deep copy to avoid modifying original
    import copy
    clean_schema = copy.deepcopy(input_schema)
    
    # Recursively remove '$schema' keys
    def _clean_json_schema(schema_obj):
        if isinstance(schema_obj, dict):
            # Remove invalid keys for Gemini
            schema_obj.pop("$schema", None)
            # schema_obj.pop("additionalProperties", None) # Optional, Gemini might support it or strict mode
            
            for key, value in schema_obj.items():
                _clean_json_schema(value)
        elif isinstance(schema_obj, list):
            for item in schema_obj:
                _clean_json_schema(item)

    _clean_json_schema(clean_schema)
    
    # Ensure type is present (usually "object")
    if "type" not in clean_schema:
        clean_schema["type"] = "object"
        
    return {
        "name": name,
        "description": description,
        "parameters": clean_schema
    }

def convert_mcp_tools_to_gemini(mcp_tools: List[Any]) -> List[Dict[str, Any]]:
    """
    Convert a list of MCP tools to Gemini Tool definitions.
    
    Returns a list containing a single Tool object with function_declarations.
    Structure:
    [
        {
            "function_declarations": [ ... ]
        }
    ]
    """
    funcs = [convert_mcp_tool_to_gemini(t) for t in mcp_tools]
    return funcs
