#!/usr/bin/env python3
"""
MCP stdio Server - Hexagonal Architecture

Stdio transport server for Claude Code integration.
Uses same hexagonal architecture as HTTP server.

Run with: poetry run mcp-edgar-ux
       or: make stdio
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .container import Container
from .adapters.mcp import TOOL_SCHEMAS, MCPHandlers
from .formatters import (
    format_fetch_filing,
    format_search_filing,
    format_list_filings,
    format_financial_statements,
    format_insider_activity
)

# Configure logging to stderr (stdout is for MCP protocol)
logging.basicConfig(
    level=logging.WARNING,  # Less verbose for stdio
    format="[%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]  # Goes to stderr by default
)

logger = logging.getLogger(__name__)

# Configuration
DEFAULT_CACHE_DIR = "/var/idio-mcp-cache/sec-filings"
DEFAULT_USER_AGENT = "breed research breed@idio.sh"


def get_cache_dir() -> Path:
    """Get cache directory from environment or use default"""
    cache_str = os.environ.get("CACHE_DIR", DEFAULT_CACHE_DIR)
    return Path(cache_str)


def get_user_agent() -> str:
    """Get user agent from environment or use default"""
    return os.environ.get("USER_AGENT", DEFAULT_USER_AGENT)


# Initialize dependency injection container
container = Container(
    cache_dir=get_cache_dir(),
    user_agent=get_user_agent()
)

# Initialize MCP handlers
handlers = MCPHandlers(container)

# MCP Server instance
mcp_server = Server("edgar-ux-mcp")


@mcp_server.list_tools()  # type: ignore[misc,no-untyped-call]
async def list_tools() -> list[Tool]:
    """List available MCP tools"""
    return [
        Tool(**TOOL_SCHEMAS["fetch_filing"]),
        Tool(**TOOL_SCHEMAS["search_filing"]),
        Tool(**TOOL_SCHEMAS["list_filings"]),
        Tool(**TOOL_SCHEMAS["get_financial_statements"]),
        Tool(**TOOL_SCHEMAS["insider_activity"])
    ]


@mcp_server.call_tool()  # type: ignore[misc,no-untyped-call]
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls"""
    logger.debug(f"call_tool: {name} args={arguments}")

    try:
        result = await _dispatch_tool(name, arguments)
    except Exception as e:
        logger.error(f"call_tool: {name} FAILED: {e}")
        raise

    # Format result
    formatters = {
        "fetch_filing": format_fetch_filing,
        "search_filing": format_search_filing,
        "list_filings": format_list_filings,
        "get_financial_statements": format_financial_statements,
        "insider_activity": format_insider_activity
    }

    formatter = formatters.get(name)
    if formatter:
        formatted_text = formatter(result)
    else:
        import json
        formatted_text = json.dumps(result, indent=2)

    logger.debug(f"call_tool: {name} returning {len(formatted_text)} chars")
    return [TextContent(type="text", text=formatted_text)]


async def _dispatch_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Dispatch tool call to appropriate handler"""
    if name == "fetch_filing":
        return await handlers.fetch_filing(
            ticker=arguments["ticker"],
            form_type=arguments["form_type"],
            date=arguments.get("date"),
            format=arguments.get("format", "text"),
            preview_lines=arguments.get("preview_lines", 200),
            force_refetch=arguments.get("force_refetch", False)
        )

    elif name == "search_filing":
        return await handlers.search_filing(
            ticker=arguments["ticker"],
            form_type=arguments["form_type"],
            pattern=arguments["pattern"],
            date=arguments.get("date"),
            format=arguments.get("format", "text"),
            context_lines=arguments.get("context_lines", 2),
            max_results=arguments.get("max_results", 20),
            offset=arguments.get("offset", 0)
        )

    elif name == "list_filings":
        return await handlers.list_filings(
            ticker=arguments.get("ticker"),
            form_type=arguments["form_type"],
            start=arguments.get("start", 0),
            max=arguments.get("max", 15),
            since=arguments.get("since")
        )

    elif name == "insider_activity":
        return await handlers.insider_activity(
            ticker=arguments["ticker"],
            days=arguments.get("days", 30)
        )

    elif name == "get_financial_statements":
        return await handlers.get_financial_statements(
            ticker=arguments["ticker"],
            statement_type=arguments.get("statement_type", "all")
        )

    else:
        raise ValueError(f"Unknown tool: {name}")


async def run_server():
    """Run the MCP server with stdio transport"""
    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options()
        )


def main():
    """Main entry point for stdio server"""
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
