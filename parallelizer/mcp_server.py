from __future__ import annotations

from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .service import ParallelizerService

mcp = FastMCP("parallelizer")


@mcp.tool()
def create_subagent(prompt: str, name: Optional[str] = None, agent: Optional[str] = None) -> dict:
    """Create a worktree and start a background coding agent."""
    service = ParallelizerService(Path.cwd())
    record = service.create_subagent(prompt=prompt, name=name, agent=agent, background=True)
    return record.to_dict()


@mcp.tool()
def create_tree(prompt: Optional[str] = None, name: Optional[str] = None) -> dict:
    """Create a worktree without starting an agent."""
    service = ParallelizerService(Path.cwd())
    record = service.create_tree(name=name, prompt=prompt or "")
    return record.to_dict()


@mcp.tool()
def list_worktrees() -> list[dict]:
    """List Parallelizer worktrees and agent status."""
    service = ParallelizerService(Path.cwd())
    return [record.to_dict() for record in service.list_records()]


@mcp.tool()
def open_worktree_info(name: str) -> dict:
    """Return path and status information for a worktree."""
    service = ParallelizerService(Path.cwd())
    return service.worktree_info(name)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
