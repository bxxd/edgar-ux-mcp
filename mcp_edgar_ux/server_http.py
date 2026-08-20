#!/usr/bin/env python3
"""
MCP HTTP/SSE Server - Hexagonal Architecture

Clean HTTP/SSE server using dependency injection and hexagonal architecture.

Run with: make server (or: poetry run uvicorn mcp_edgar_ux.server_http:app --host 127.0.0.1 --port 5002)

Configuration:
- PORT: Server port (default: 5002)
- CACHE_DIR: Filing cache directory (default: /var/idio-mcp-cache/sec-filings)
"""

import logging
import os
import signal
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from .container import Container
from .adapters.mcp import TOOL_SCHEMAS, MCPHandlers
from .formatters import (
    format_fetch_filing,
    format_search_filing,
    format_list_filings,
    format_list_documents,
    format_financial_statements,
    format_insider_activity,
    format_thirteenf_holdings
)

# Configure logging with millisecond precision
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y/%m/%d %H:%M:%S"
)


class MillisecondFormatter(logging.Formatter):
    """Custom formatter with milliseconds as :XXXX format"""
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        """Override formatTime to include milliseconds with : separator"""
        ct = datetime.fromtimestamp(record.created, tz=timezone.utc)
        if datefmt:
            s = ct.strftime("%Y/%m/%d %H:%M:%S")
            ms = int((record.created % 1) * 10000)
            return f"{s}:{ms:04d}"
        return super().formatTime(record, datefmt)


# Apply custom formatter to root logger
for handler in logging.root.handlers:
    handler.setFormatter(MillisecondFormatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y/%m/%d %H:%M:%S"
    ))

logger = logging.getLogger(__name__)

# Configuration
DEFAULT_PORT = 5002
DEFAULT_CACHE_DIR = "/var/idio-mcp-cache/sec-filings"
DEFAULT_USER_AGENT = "breed research breed@idio.sh"


def get_port() -> int:
    """Get server port from environment or use default"""
    port_str = os.environ.get("PORT", str(DEFAULT_PORT))
    try:
        return int(port_str)
    except ValueError:
        msg = f"Invalid PORT value: {port_str}"
        raise ValueError(msg) from None


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

# SSE transport for multi-client support
sse_transport = SseServerTransport("/messages")

# Streamable HTTP transport — stateless, survives server restarts
# (SSE sessions die on restart; each streamable call is self-contained)
streamable_transport = StreamableHTTPServerTransport(
    mcp_session_id=None,
    is_json_response_enabled=True,
)


@mcp_server.list_tools()  # type: ignore[misc,no-untyped-call]
async def list_tools() -> list[Tool]:
    """List available MCP tools"""
    return [
        Tool(**TOOL_SCHEMAS["fetch_filing"]),
        Tool(**TOOL_SCHEMAS["search_filing"]),
        Tool(**TOOL_SCHEMAS["list_filings"]),
        Tool(**TOOL_SCHEMAS["list_documents"]),
        Tool(**TOOL_SCHEMAS["thirteenf_holdings"]),
        Tool(**TOOL_SCHEMAS["get_financial_statements"]),
        Tool(**TOOL_SCHEMAS["insider_activity"])
    ]


@mcp_server.call_tool()  # type: ignore[misc,no-untyped-call]
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls"""
    logger.info(f"call_tool: {name} args={arguments}")

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
        "list_documents": format_list_documents,
        "thirteenf_holdings": format_thirteenf_holdings,
        "get_financial_statements": format_financial_statements,
        "insider_activity": format_insider_activity
    }

    formatter = formatters.get(name)
    if formatter:
        formatted_text = formatter(result)
    else:
        import json
        formatted_text = json.dumps(result, indent=2)

    logger.info(f"call_tool: {name} returning {len(formatted_text)} chars")
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
            force_refetch=arguments.get("force_refetch", False),
            document=arguments.get("document")
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

    elif name == "list_documents":
        return await handlers.list_documents(
            ticker=arguments["ticker"],
            form_type=arguments["form_type"],
            date=arguments.get("date")
        )

    elif name == "thirteenf_holdings":
        return await handlers.thirteenf_holdings(
            ticker=arguments["ticker"],
            date=arguments.get("date"),
            form_type=arguments.get("form_type", "13F-HR"),
            max_holdings=arguments.get("max_holdings", 50)
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


# HTTP routes
async def handle_ping(request: Request) -> Response:
    """Health check endpoint"""
    return JSONResponse({"status": "ok"})


async def handle_sse(request: Request) -> Response:
    """SSE endpoint for MCP communication"""
    client_addr = request.client.host if request.client else "unknown"
    logger.info(f"SSE connect from {client_addr}")
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        logger.info(f"SSE session started for {client_addr}")
        await mcp_server.run(
            streams[0], streams[1], mcp_server.create_initialization_options()
        )
        logger.info(f"SSE disconnect from {client_addr}")
    return Response()


@asynccontextmanager
async def lifespan(app):
    """Run the MCP server against the streamable transport for the app's lifetime."""
    async with streamable_transport.connect() as (read_stream, write_stream):
        async with anyio.create_task_group() as tg:
            async def run_streamable():
                await mcp_server.run(
                    read_stream, write_stream, mcp_server.create_initialization_options(),
                    stateless=True,
                )

            tg.start_soon(run_streamable)
            yield
            tg.cancel_scope.cancel()


routes = [
    Route("/ping", handle_ping),
    Mount("/mcp", app=streamable_transport.handle_request),
    Route("/sse", handle_sse),
    Mount("/messages", app=sse_transport.handle_post_message),
]

app = Starlette(debug=True, routes=routes, lifespan=lifespan)


# Graceful shutdown on SIGTERM
def handle_sigterm(signum, frame):
    logger.info("Received SIGTERM, shutting down gracefully...")
    import sys
    sys.exit(0)


signal.signal(signal.SIGTERM, handle_sigterm)


if __name__ == "__main__":
    import uvicorn
    port = get_port()
    logger.info(f"Starting MCP HTTP server on port {port}")
    uvicorn.run(app, host="127.0.0.1", port=port)
