from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import typer

from .config import write_global_default_agent
from .errors import ParallelizerError
from .models import TreeRecord
from .prompts import manager_prompt, plr_instructions_markdown, setup_plr_prompt
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


@app.command()
def init(
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Default agent: codex or claude."),
) -> None:
    """Initialize global Parallelizer config."""
    try:
        selected = agent or typer.prompt("Default coding agent [codex/claude]", default="codex")
        path = write_global_default_agent(selected.strip())
        typer.echo(f"Wrote global config: {path}")
    except ParallelizerError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command("sub")
@app.command("subagent")
def spawn_subagent(
    name: Optional[str] = typer.Argument(None, help="Optional worktree name."),
    prompt_parts: Optional[List[str]] = typer.Argument(None, help="Prompt text."),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Agent name from config."),
    background: bool = typer.Option(False, "--background", help="Start as a background process."),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model to pass to codex/claude."),
    agent_arg: Optional[List[str]] = typer.Option(None, "--agent-arg", help="Extra raw agent argument."),
) -> None:
    """Create a worktree and start a subagent."""
    _run_cli(lambda service: _sub(service, name, prompt_parts or [], agent, background, model, agent_arg or []))


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
    """Open a worktree in a new tmux pane."""
    _run_cli(lambda service: _open_tmux(service, name))


@app.command()
def instructions() -> None:
    """Print markdown instructions for coding agents."""
    typer.echo(plr_instructions_markdown().rstrip())


@agent_app.command()
def manager(
    prompt_parts: Optional[List[str]] = typer.Argument(None, help="Task prompt."),
    interval: int = typer.Option(10, "--interval", "-i", min=1, help="Monitoring interval in seconds."),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Agent name from config."),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model to pass to codex/claude."),
    agent_arg: Optional[List[str]] = typer.Option(None, "--agent-arg", help="Extra raw agent argument."),
) -> None:
    """Start an interactive manager agent for coordinating subagents."""
    _run_cli(lambda service: _agent_manager(service, prompt_parts or [], interval, agent, model, agent_arg or []))


@agent_app.command("setup_plr")
def setup_plr(
    prompt_parts: Optional[List[str]] = typer.Argument(None, help="Additional setup guidance."),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Agent name from config."),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model to pass to codex/claude."),
    agent_arg: Optional[List[str]] = typer.Option(None, "--agent-arg", help="Extra raw agent argument."),
) -> None:
    """Start an interactive agent to configure this repo for Parallelizer."""
    _run_cli(lambda service: _agent_setup_plr(service, prompt_parts or [], agent, model, agent_arg or []))


def _tree(service: ParallelizerService, name: Optional[str], prompt_parts: List[str]) -> None:
    record = service.create_tree(name=name, prompt=_prompt_from_args(prompt_parts))
    typer.echo(record.worktree_path)


def _sub(
    service: ParallelizerService,
    name: Optional[str],
    prompt_parts: List[str],
    agent: Optional[str],
    background: bool,
    model: Optional[str],
    agent_args: List[str],
) -> None:
    prompt = _prompt_from_args(prompt_parts)
    record = service.create_subagent(
        prompt=prompt,
        name=name,
        agent=agent,
        background=background,
        model=model,
        agent_args=agent_args,
    )
    if background:
        typer.echo(f"{record.name}\t{record.status}\t{record.worktree_path}\tlog={record.log_path}")
        return
    command = service.interactive_command(record)
    os.chdir(record.worktree_path)
    os.execvp(command[0], command)


def _agent_manager(
    service: ParallelizerService,
    prompt_parts: List[str],
    interval: int,
    agent: Optional[str],
    model: Optional[str],
    agent_args: List[str],
) -> None:
    prompt = manager_prompt(_prompt_from_args(prompt_parts), interval)
    _exec_current_repo_agent(service, prompt, agent, model, agent_args)


def _agent_setup_plr(
    service: ParallelizerService,
    prompt_parts: List[str],
    agent: Optional[str],
    model: Optional[str],
    agent_args: List[str],
) -> None:
    prompt = setup_plr_prompt(_prompt_from_args(prompt_parts))
    _exec_current_repo_agent(service, prompt, agent, model, agent_args)


def _exec_current_repo_agent(
    service: ParallelizerService,
    prompt: str,
    agent: Optional[str],
    model: Optional[str],
    agent_args: List[str],
) -> None:
    command = service.current_repo_agent_command(prompt, agent=agent, model=model, agent_args=agent_args)
    os.chdir(service.project.root)
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
    selected = _resolve_record_name(records, name)
    info = service.worktree_info(selected)
    typer.echo(info["worktree_path"])


def _open_tmux(service: ParallelizerService, name: Optional[str]) -> None:
    if not os.environ.get("TMUX"):
        raise ParallelizerError("plr open requires an active tmux session. Use `plr cd NAME` outside tmux.")
    if not shutil.which("tmux"):
        raise ParallelizerError("tmux was not found on PATH.")
    records = service.list_records()
    if not records:
        raise ParallelizerError("No Parallelizer worktrees found.")
    selected = _resolve_record_name(records, name)
    info = service.worktree_info(selected)
    result = subprocess.run(
        ["tmux", "split-window", "-c", info["worktree_path"]],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise ParallelizerError(f"tmux split-window failed for {info['worktree_path']}: {message}")


def _resolve_record_name(records: List[TreeRecord], name: Optional[str]) -> str:
    if name:
        if not any(record.name == name for record in records):
            raise ParallelizerError(f"Unknown worktree: {name}")
        return name
    if not sys.stdin.isatty():
        raise ParallelizerError("A worktree name is required when stdin is not interactive.")
    if shutil.which("fzf"):
        selected = _select_record_fzf(records)
        if selected:
            return selected
    return _select_record_numbered(records)


def _select_record_fzf(records: List[TreeRecord]) -> Optional[str]:
    rows = "\n".join(f"{record.name}\t{record.status}\t{record.worktree_path}" for record in records)
    result = subprocess.run(
        ["fzf", "--with-nth=1,2,3"],
        input=rows,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.split("\t", 1)[0].strip()


def _select_record_numbered(records: List[TreeRecord]) -> str:
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
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
