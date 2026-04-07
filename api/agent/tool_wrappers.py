"""
Dynamically generates LangChain StructuredTools from the MCP server's exposed tools.

KEY SECURITY DESIGN:
  - Fetches the tool list and JSON schemas directly from the running MCP server.
  - Strips the `access_context` parameter out of the schema dynamically.
  - Creates a Pydantic model on the fly so the LLM never sees the context parameter.
  - Injects `access_context` invisibly when the tool is actually called.
"""

import json
import logging
from typing import Any

from langchain_core.tools import StructuredTool
from mcp import ClientSession
from pydantic import create_model, Field



logger = logging.getLogger(__name__)



def _parse_tool_result(result: Any) -> dict:
    """Parse the MCP tool call result into a Python dict."""
    if hasattr(result, "content"):
        content = result.content
        if isinstance(content, list) and content:
            text = content[0].text if hasattr(content[0], "text") else str(content[0])
        else:
            text = str(content)
    else:
        text = str(result)

    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"raw": text}


async def create_secure_tools(
    session: ClientSession,
    access_context_json: str,
) -> list[StructuredTool]:
    """
    Dynamically discover tools from the MCP server, hide the access_context parameter,
    and build LangChain StructuredTools.
    """
    logger.debug("Fetching tool list from MCP server...")

    # 1. Ask the MCP server for its available tools
    try:
        response = await session.list_tools()
        logger.debug(f"MCP server returned {len(response.tools)} tools")
        logger.debug(f"MCP tool names: {[t.name for t in response.tools]}")
        if not response.tools:
            logger.error("MCP server returned ZERO tools. Check that your MCP server registered its tools correctly.")
            return []
            
    except Exception as e:
        logger.error(f"Failed to list tools from MCP server: {e}", exc_info=True)
        return []
    tools = []

    for tool_def in response.tools:
        tool_name = tool_def.name
        description = tool_def.description
        schema = tool_def.inputSchema

        # 2. Build a dynamic Pydantic schema (excluding access_context)
        fields = {}
        for prop_name, prop_info in schema.get("properties", {}).items():
            if prop_name == "access_context":
                continue
            
            # Map JSON schema types to Python types
            json_type = prop_info.get("type", "string")
            if json_type == "string":
                py_type = str
            elif json_type == "integer":
                py_type = int
            elif json_type == "number":
                py_type = float
            elif json_type == "boolean":
                py_type = bool
            elif json_type == "array":
                py_type = list[str]  # Simplified for our clinical trial IDs
            elif json_type == "object":
                py_type = dict
            else:
                py_type = Any
            
            is_required = prop_name in schema.get("required", [])
            
            # Extract defaults provided by the server, or None if optional
            default_val = prop_info.get("default", ... if is_required else None)
            
            # Allow None types for optional fields
            if default_val is None and not is_required:
                py_type = py_type | None
            
            # Add to Pydantic fields dictionary
            fields[prop_name] = (
                py_type, 
                Field(default=default_val, description=prop_info.get("description", ""))
            )

        # Create the Pydantic class in memory
        DynamicSchema = create_model(f"{tool_name}Input", **fields)

        # 3. Create the execution closure
        def make_caller(t_name: str):
            async def _caller(**kwargs) -> dict:
                # Secretly inject the authorization context
                kwargs["access_context"] = access_context_json
                
                # Remove None values so MCP uses its native defaults
                clean_kwargs = {k: v for k, v in kwargs.items() if v is not None}
                
                try:
                    result = await session.call_tool(t_name, arguments=clean_kwargs)
                    return _parse_tool_result(result)
                except Exception as e:
                    logger.error(f"MCP tool call failed: tool={t_name} error={e}")
                    return {"error": str(e), "tool": t_name}
            
            _caller.__name__ = t_name
            _caller.__doc__ = description
            return _caller

        # 4. Wrap it in a LangChain StructuredTool
        structured_tool = StructuredTool.from_function(
            coroutine=make_caller(tool_name),
            name=tool_name,
            description=description,
            args_schema=DynamicSchema,
            return_direct=False,
        )
        tools.append(structured_tool)

    logger.info(f"Dynamically loaded {len(tools)} tools from MCP server")
    return tools