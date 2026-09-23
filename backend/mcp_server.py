"""War Room OS context engine as an MCP server, for coding agents (Claude Code, Cursor, ...).

Run:  pyenv/bin/python -m backend.mcp_server
Add to Claude Code:  claude mcp add warroom -- /path/to/war-room-os/pyenv/bin/python -m backend.mcp_server
Set WARROOM_PROJECT to the project id shown on the project page.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

from mcp.server.mcpserver import MCPServer  # noqa: E402

from . import context as ctx, engine  # noqa: E402

PROJECT = os.getenv("WARROOM_PROJECT", "")
ctx.load_cards(PROJECT)

server = MCPServer(
    name="warroom",
    instructions="Repository intelligence for coding agents. Call understand or context before exploring with grep; "
                 "call precedent before writing new code; call impact before editing a shared symbol.",
)


def _text(out: dict) -> str:
    return out["text"] + f"\n\n[{out['tokens']} tokens]"


@server.tool(description="Current flow, where the code lives, data touched, recent changes and owners for a task.")
def understand(task: str, budget: int = 1500) -> str:
    return _text(engine.pack(engine.understand(PROJECT, task), budget))


@server.tool(description="Where the codebase already does something similar: implementation, tests, endpoint and registration to copy.")
def precedent(task: str, budget: int = 1200) -> str:
    return _text(engine.pack(engine.find_precedent(PROJECT, task), budget))


@server.tool(description="What a change to a file, symbol or field touches: callers, endpoints, data, tests, co-changed files, people.")
def impact(target: str, budget: int = 1500) -> str:
    return _text(engine.pack(engine.impact(PROJECT, target), budget))


@server.tool(description="Repository layering: where routes, services, persistence, integrations and tests live.")
def conventions() -> str:
    return _text(engine.pack(engine.conventions(PROJECT), 800))


@server.tool(description="Stateful context. Pass files you inspected, symbols you edited and failing tests; returns what became relevant.")
def context(task: str, inspected: list[str] | None = None, edited: list[str] | None = None,
            failing_tests: list[str] | None = None, budget: int = 2000) -> str:
    state = {"inspected": inspected or [], "edited": edited or [], "failing_tests": failing_tests or []}
    return _text(engine.context(PROJECT, task, state, budget))


if __name__ == "__main__":
    server.run("stdio")
