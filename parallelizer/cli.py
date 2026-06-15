from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

import typer

from .errors import ParallelizerError
from .service import ParallelizerService

app = typer.Typer(no_args_is_help=True, help="Spawn coding subagents in git worktrees.")
agent_app = typer.Typer(help="Agent management helpers.")
app.add_typer(agent_app, name="agent")


def main() -> None:
    app()


@app.command()
def tree(
    name: Optional[str] = typer.Argument(None, help="Optional worktree name."),
    prompt_parts: Optional[List[str]] = typer.Argument(None, help="Optional prompt text."),
) -> None:
    """Create a worktree without starting an agent."""
    _run_cli(lambda service: _tree(service, name, prompt_parts or []))


@app.command("sub")
@app.command("subagent")
def spawn_subagent(
    name: Optional[str] = typer.Argument(None, help="Optional worktree name."),
    prompt_parts: Optional[List[str]] = typer.Argument(None, help="Prompt text."),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Agent name from config."),
    background: bool = typer.Option(False, "--background", help="Start as a background process."),
) -> None:
    """Create a worktree and start a subagent."""
    _run_cli(lambda service: _sub(service, name, prompt_parts or [], agent, background))


@app.command("ls")
def list_worktrees() -> None:
    """List Parallelizer worktrees and agent status."""
    _run_cli(_list_worktrees)


@app.command("cd")
def cd_worktree(name: Optional[str] = typer.Argument(None, help="Worktree name.")) -> None:
    """Print a worktree path for shell cd command substitution."""
    _run_cli(lambda service: _cd(service, name))


@app.command()
def open(name: Optional[str] = typer.Argument(None, help="Worktree name.")) -> None:
    """Placeholder for future tmux helpers."""
    raise typer.BadParameter("plr open is not implemented yet.")


@agent_app.command()
def manager() -> None:
    """Placeholder for future manager agent."""
    raise typer.BadParameter("plr agent manager is not implemented yet.")


@agent_app.command("setup_plr")
def setup_plr() -> None:
    """Placeholder for future setup agent."""
    raise typer.BadParameter("plr agent setup_plr is not implemented yet.")


def _tree(service: ParallelizerService, name: Optional[str], prompt_parts: List[str]) -> None:
    record = service.create_tree(name=name, prompt=_prompt_from_args(prompt_parts))
    typer.echo(record.worktree_path)


def _sub(
    service: ParallelizerService,
    name: Optional[str],
    prompt_parts: List[str],
    agent: Optional[str],
    background: bool,
) -> None:
    prompt = _prompt_from_args(prompt_parts)
    record = service.create_subagent(prompt=prompt, name=name, agent=agent, background=background)
    if background:
        typer.echo(f"{record.name}\t{record.status}\t{record.worktree_path}\tlog={record.log_path}")
        return
    command = service.interactive_command(record)
    os.chdir(record.worktree_path)
    os.execvp(command[0], command)


def _list_worktrees(service: ParallelizerService) -> None:
    records = service.list_records()
    if not records:
        typer.echo("No Parallelizer worktrees found.")
        return
    typer.echo("NAME\tSTATUS\tAGENT\tPID\tBRANCH\tPATH\tLOG")
    for record in records:
        typer.echo(
            "\t".join(
                [
                    record.name,
                    record.status,
                    record.agent or "-",
                    str(record.pid or "-"),
                    record.branch,
                    record.worktree_path,
                    record.log_path or "-",
                ]
            )
        )


def _cd(service: ParallelizerService, name: Optional[str]) -> None:
    records = service.list_records()
    if not records:
        raise ParallelizerError("No Parallelizer worktrees found.")
    selected = name or _select_record(records)
    info = service.worktree_info(selected)
    typer.echo(info["worktree_path"])


def _select_record(records: list) -> str:
    for index, record in enumerate(records, start=1):
        typer.echo(f"{index}. {record.name}\t{record.status}\t{record.worktree_path}", err=True)
    choice = typer.prompt("Select worktree", type=int, err=True)
    if choice < 1 or choice > len(records):
        raise ParallelizerError("Invalid worktree selection.")
    return records[choice - 1].name


def _prompt_from_args(parts: List[str]) -> str:
    if parts:
        return " ".join(parts)
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def _run_cli(callback) -> None:
    try:
        service = ParallelizerService(Path.cwd())
        callback(service)
    except ParallelizerError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
