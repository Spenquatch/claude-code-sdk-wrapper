#!/usr/bin/env python3
"""
Configurable MCP approval server that reads strategy from environment or config file.
"""

import asyncio
import datetime
import json
import os
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from .strategies import create_approval_strategy

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

F = TypeVar("F", bound=Callable[..., Any])

# Try to import FastMCP
try:
    from mcp.server.fastmcp import FastMCP

    HAS_FASTMCP = True
except ImportError:
    HAS_FASTMCP = False
    print(
        "Warning: FastMCP not available, using fallback implementation", file=sys.stderr
    )


# Fallback implementation
class SimpleMCP:
    def __init__(self, name: str) -> None:
        self.name = name
        self.tools: dict[str, Any] = {}

    def tool(self) -> Callable[[F], F]:
        def decorator(func: F) -> F:
            self.tools[func.__name__] = func
            return func

        return decorator

    async def run(self) -> None:
        # Simple stdio server implementation
        while True:
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, sys.stdin.readline
                )
                if not line:
                    break

                request = json.loads(line)
                method = request.get("method", "")

                if method == "tools/list":
                    response = {
                        "id": request.get("id"),
                        "result": {
                            "tools": [{"name": name} for name in self.tools.keys()]
                        },
                    }
                elif method == "tools/call":
                    tool_name = request["params"]["name"]
                    if tool_name in self.tools:
                        result = await self.tools[tool_name](
                            **request["params"]["arguments"]
                        )
                        response = {"id": request.get("id"), "result": result}
                    else:
                        response = {
                            "id": request.get("id"),
                            "error": {
                                "code": -32601,
                                "message": "Method not found",
                            },
                        }
                else:
                    response = {
                        "id": request.get("id"),
                        "error": {"code": -32601, "message": "Method not found"},
                    }

                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()

            except Exception as e:
                sys.stderr.write(f"Error: {e}\n")
                sys.stderr.flush()


# Create MCP server
mcp: Any
if HAS_FASTMCP:
    mcp = FastMCP("approval-server")
else:
    mcp = SimpleMCP("approval-server")


def log_to_file(message: str) -> None:
    """Simple file logging to verify the approval function is called"""
    log_path = os.environ.get("APPROVAL_LOG_PATH", "approval_log.txt")
    with open(log_path, "a") as f:
        f.write(f"{datetime.datetime.now()} - {message}\n")


def load_strategy_config() -> Any:
    """Load strategy configuration from environment or file."""
    # Try environment variable first
    config_json = os.environ.get("APPROVAL_STRATEGY_CONFIG")
    if config_json:
        return json.loads(config_json)

    # Try config file
    config_file = os.environ.get("APPROVAL_CONFIG_FILE")
    if config_file and os.path.exists(config_file):
        with open(config_file) as f:
            return json.load(f)

    # Default to allowlist with no tools (deny all)
    return {"type": "allowlist", "allowlist": []}


# Load strategy configuration
strategy_config = load_strategy_config()
strategy = create_approval_strategy(strategy_config["type"], strategy_config)


@mcp.tool()  # type: ignore[misc]
async def permissions__approve(tool_name: str, input: dict, reason: str = "") -> dict:
    """
    Approve or deny permission requests from Claude.

    Returns dict with behavior:"allow"/"deny"
    """
    # Use the strategy to make the decision
    approved = strategy.should_approve(tool_name, input)

    # Log the decision
    log_to_file(f"Tool: {tool_name}, Approved: {approved}, Input: {json.dumps(input)}")

    if approved:
        return {"behavior": "allow", "updatedInput": input}
    else:
        return {"behavior": "deny", "message": strategy.get_denial_reason(tool_name)}


if __name__ == "__main__":
    # Run the server
    if HAS_FASTMCP:
        asyncio.run(mcp.run())
    else:
        # Fallback stdio server
        try:
            asyncio.run(mcp.run())
        except KeyboardInterrupt:
            pass
