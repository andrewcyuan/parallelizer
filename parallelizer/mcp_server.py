from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

from .prompts import plr_instructions_markdown
from .service import ParallelizerService

mcp = FastMCP("parallelizer")


def _service() -> ParallelizerService:
    return ParallelizerService(Path.cwd())


@mcp.tool()
def create_subagent(
    prompt: str,
    name: Optional[str] = None,
    agent: Optional[str] = None,
    model: Optional[str] = None,
    agent_args: Optional[List[str]] = None,
) -> dict:
    """Create a worktree and start a background coding agent on the given prompt."""
    record = _service().create_subagent(
        prompt=prompt,
        name=name,
        agent=agent,
        background=True,
        model=model,
        agent_args=agent_args,
    )
    return record.to_dict()


@mcp.tool()
def create_tree(prompt: Optional[str] = None, name: Optional[str] = None) -> dict:
    """Create a worktree without starting an agent."""
    record = _service().create_tree(name=name, prompt=prompt or "")
    return record.to_dict()


@mcp.tool()
def list_worktrees() -> list[dict]:
    """List Parallelizer worktrees and their agent status."""
    return [record.to_dict() for record in _service().list_records()]


@mcp.tool()
def worktree_info(name: str) -> dict:
    """Return path and status information for a single worktree."""
    return _service().worktree_info(name)


@mcp.tool()
def run_in_worktree(name: str, command: List[str]) -> dict:
    """Run a command inside a worktree and return its stdout, stderr, and exit code.

    Use this to inspect or operate on a worktree, e.g. ["git", "status", "--short"]
    or ["git", "diff"].
    """
    return _service().run_in_worktree(name, command)


@mcp.tool()
def merge_worktree(
    name: str,
    no_ff: bool = False,
    squash: bool = False,
    force: bool = False,
) -> dict:
    """Merge a worktree branch into the current branch, then remove the worktree."""
    record = _service().merge_tree(name, no_ff=no_ff, squash=squash, force_cleanup=force)
    return record.to_dict()


@mcp.tool()
def remove_worktree(name: str, force: bool = False) -> dict:
    """Run cleanup and remove a Parallelizer worktree."""
    record = _service().remove_tree(name, force_cleanup=force)
    return record.to_dict()


@mcp.tool()
def instructions() -> str:
    """Return the agent-facing Parallelizer usage instructions as markdown."""
    return plr_instructions_markdown().rstrip()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
