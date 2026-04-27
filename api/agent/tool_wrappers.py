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
import time
from typing import Any

import structlog
from langchain_core.tools import StructuredTool
from mcp import ClientSession
from pydantic import create_model, Field

logger = structlog.get_logger(__name__)

MAX_ARG_LOG_CHARS = 2000
MAX_RESULT_LOG_CHARS = 2000


def _preview_text(value: Any, max_chars: int) -> str:
    """Render a bounded string representation suitable for logs."""
    text = str(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "... [truncated]"



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


def _sanitize_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Remove hidden access context from logs and avoid oversized payloads."""
    return {
        k: v
        for k, v in kwargs.items()
        if k != "access_context"
    }


def _map_json_schema_to_python_type(prop_info: dict[str, Any]) -> Any:
    """Map JSON Schema property definitions to Python types for Pydantic models."""
    if "anyOf" in prop_info:
        mapped = []
        for option in prop_info.get("anyOf", []):
            option_type = option.get("type")
            if option_type == "string":
                mapped.append(str)
            elif option_type == "integer":
                mapped.append(int)
            elif option_type == "number":
                mapped.append(float)
            elif option_type == "boolean":
                mapped.append(bool)
            elif option_type == "array":
                mapped.append(list[str])
            elif option_type == "object":
                mapped.append(dict)
            elif option_type == "null":
                mapped.append(type(None))

        if mapped:
            py_type = mapped[0]
            for t in mapped[1:]:
                py_type = py_type | t
            return py_type

    json_type = prop_info.get("type", "string")
    if json_type == "string":
        return str
    if json_type == "integer":
        return int
    if json_type == "number":
        return float
    if json_type == "boolean":
        return bool
    if json_type == "array":
        return list[str]
    if json_type == "object":
        return dict
    return Any


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
        logger.debug("mcp_tool_catalog_received", count=len(response.tools))
        if not response.tools:
            logger.error("mcp_tool_catalog_empty")
            return []
            
    except Exception as e:
        logger.error("mcp_tool_catalog_failed", error=str(e), exc_info=True)
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

            # Map JSON schema types to Python types (supports anyOf unions).
            py_type = _map_json_schema_to_python_type(prop_info)
            
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
                public_kwargs = _sanitize_args(clean_kwargs)
                started = time.perf_counter()

                logger.info(
                    "tool_invocation",
                    tool=t_name,
                    arguments_preview=_preview_text(public_kwargs, MAX_ARG_LOG_CHARS),
                )
                
                try:
                    result = await session.call_tool(t_name, arguments=clean_kwargs)
                    parsed = _parse_tool_result(result)
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    logger.info(
                        "tool_result",
                        tool=t_name,
                        duration_ms=duration_ms,
                        result_preview=_preview_text(parsed, MAX_RESULT_LOG_CHARS),
                        status=("error" if isinstance(parsed, dict) and parsed.get("error") else "success"),
                    )
                    return parsed
                except Exception as e:
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    logger.error(
                        "tool_failed",
                        tool=t_name,
                        duration_ms=duration_ms,
                        arguments_preview=_preview_text(public_kwargs, MAX_ARG_LOG_CHARS),
                        error=str(e),
                    )
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

    logger.info("mcp_tools_loaded", count=len(tools))
    return tools