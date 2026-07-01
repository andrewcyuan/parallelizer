from __future__ import annotations

import builtins
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .agent_event import apply_event
from .config import write_global_default_agent
from .errors import ParallelizerError
from .git_utils import repo_root
from .models import TreeRecord
from .prompts import manager_prompt, plr_instructions_markdown, setup_plr_prompt
from .service import ParallelizerService

app = typer.Typer(no_args_is_help=True, help="Spawn coding subagents in git worktrees.")
agent_app = typer.Typer(help="Agent management helpers.")
app.add_typer(agent_app, name="agent")
mcp_app = typer.Typer(no_args_is_help=True, help="MCP server management helpers.")
app.add_typer(mcp_app, name="mcp")


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
    edit: bool = typer.Option(False, "--edit", help="Edit the prompt in $EDITOR before starting."),
) -> None:
    """Create a worktree and start a subagent."""
    _run_cli(lambda service: _sub(service, name, prompt_parts or [], agent, background, model, agent_arg or [], edit))


@app.command("ls")
def list_worktrees() -> None:
    """List Parallelizer worktrees and agent status."""
    _run_cli(_list_worktrees)


@app.command("cd")
def cd_worktree(name: Optional[str] = typer.Argument(None, help="Worktree name.")) -> None:
    """Start a shell in a worktree."""
    _run_cli(lambda service: _cd(service, name))


@app.command("wt", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run_in_worktree(
    name: str = typer.Argument(..., help="Worktree name."),
    command: Optional[List[str]] = typer.Argument(None, help="Command to run after --."),
) -> None:
    """Run a command in a worktree."""
    _run_cli(lambda service: _wt(service, name, command or []))


@app.command()
def open(name: Optional[str] = typer.Argument(None, help="Worktree name.")) -> None:
    """Open a worktree in a new tmux pane."""
    _run_cli(lambda service: _open_tmux(service, name))


@app.command("rm")
@app.command("remove")
def remove(
    names: Optional[List[str]] = typer.Argument(None, help="Worktree name, or '*' for all worktrees."),
    force: bool = typer.Option(False, "--force", help="Continue removal if cleanup_environment fails."),
) -> None:
    """Run optional cleanup and remove a Parallelizer worktree."""
    _run_cli(lambda service: _remove(service, names or [], force))


@app.command()
def merge(
    name: str = typer.Argument(..., help="Worktree name."),
    no_ff: bool = typer.Option(False, "--no-ff", help="Create a merge commit instead of fast-forwarding."),
    squash: bool = typer.Option(False, "--squash", help="Squash the worktree branch into the current branch."),
    force: bool = typer.Option(False, "--force", help="Continue removal if cleanup_environment fails."),
) -> None:
    """Merge a worktree branch into the current branch, then remove the worktree."""
    _run_cli(lambda service: _merge(service, name, no_ff, squash, force))


@app.command()
def instructions() -> None:
    """Print markdown instructions for coding agents."""
    typer.echo(plr_instructions_markdown().rstrip())


@mcp_app.command("add")
def mcp_add(
    agent: str = typer.Argument(..., help="Coding agent to configure: claude or codex."),
    scope: str = typer.Option("user", "--scope", help="Config scope passed to claude (claude only)."),
) -> None:
    """Register the Parallelizer MCP server with claude or codex, using this repo as the path."""
    _mcp_add(agent, scope)


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


@agent_app.command("setup")
def setup(
    prompt: Optional[str] = typer.Argument(None, help="Repo-specific setup instructions."),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="Agent name from config."),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model to pass to codex/claude."),
    agent_arg: Optional[List[str]] = typer.Option(None, "--agent-arg", help="Extra raw agent argument."),
) -> None:
    """Start an interactive agent to configure this repo for Parallelizer."""
    _run_cli(lambda service: _agent_setup_plr(service, prompt, agent, model, agent_arg or []))


@agent_app.command("event", hidden=True)
def agent_event(
    name: str = typer.Argument(..., help="Worktree name."),
    event: str = typer.Argument(..., help="Internal event name."),
    state_file: Path = typer.Option(..., "--state-file", help="Parallelizer state file."),
) -> None:
    """Record an internal background-agent hook event."""
    try:
        payload = json.loads(sys.stdin.read() or "null")
        if not isinstance(payload, dict):
            payload = None
    except json.JSONDecodeError:
        payload = None
    try:
        apply_event(state_file, name, event, payload)
    except Exception:
        return


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
    edit: bool = False,
) -> None:
    prompt = _prompt_from_args(prompt_parts)
    if edit:
        prompt = _edit_prompt(prompt)
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
    prompt: Optional[str],
    agent: Optional[str],
    model: Optional[str],
    agent_args: List[str],
) -> None:
    setup_prompt = setup_plr_prompt(_prompt_from_optional_arg(prompt))
    _exec_current_repo_agent(service, setup_prompt, agent, model, agent_args)


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
    table = Table(title="Parallelizer Worktrees", show_lines=False)
    table.add_column("Name", style="bold", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Agent", no_wrap=True)
    table.add_column("PID", justify="right", no_wrap=True)
    table.add_column("Branch", no_wrap=True)
    table.add_column("Path")
    table.add_column("Log")
    for record in records:
        table.add_row(
            record.name,
            _status_text(record.status),
            record.agent or "-",
            str(record.pid or "-"),
            record.branch,
            record.worktree_path,
            record.log_path or "-",
        )
    Console().print(table)


def _status_text(status: str) -> Text:
    styles = {
        "done": "green",
        "running": "cyan",
        "awaiting-input": "yellow",
        "error": "red",
        "no-agent": "dim",
    }
    return Text(status, style=styles.get(status, ""))


def _cd(service: ParallelizerService, name: Optional[str]) -> None:
    if name:
        info = service.worktree_info(name)
        target_path = info["worktree_path"]
    else:
        target_path = _resolve_cd_target_path(service)
    os.chdir(target_path)
    shell = os.environ.get("SHELL", "/bin/sh")
    os.execvp(shell, [Path(shell).name])


def _wt(service: ParallelizerService, name: str, command: List[str]) -> None:
    if not command:
        raise ParallelizerError("A command is required after --")
    info = service.worktree_info(name)
    result = subprocess.run(command, cwd=info["worktree_path"], check=False)
    raise typer.Exit(result.returncode)


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


def _remove(service: ParallelizerService, names: List[str], force: bool) -> None:
    if not names:
        records = service.list_records()
        if not records:
            raise ParallelizerError("No Parallelizer worktrees found.")
        selected = _resolve_record_name(records, None)
        record = service.remove_tree(selected, force_cleanup=force)
        typer.echo(f"Removed {record.name}\t{record.worktree_path}")
        return
    if names == ["*"]:
        records = service.list_records()
        if not records:
            typer.echo("No Parallelizer worktrees found.")
            return
        for record in records:
            removed = service.remove_tree(record.name, force_cleanup=force)
            typer.echo(f"Removed {removed.name}\t{removed.worktree_path}")
        return
    if "*" in names:
        raise ParallelizerError("'*' must be the only argument when removing all worktrees.")
    if len(names) > 1:
        raise ParallelizerError("Only one worktree name is accepted. Use `plr rm '*'` to remove all worktrees.")
    record = service.remove_tree(names[0], force_cleanup=force)
    typer.echo(f"Removed {record.name}\t{record.worktree_path}")


def _merge(service: ParallelizerService, name: str, no_ff: bool, squash: bool, force: bool) -> None:
    record = service.merge_tree(name, no_ff=no_ff, squash=squash, force_cleanup=force)
    typer.echo(f"Merged {record.branch} and removed {record.name}\t{record.worktree_path}")


def _mcp_add(agent: str, scope: str) -> None:
    selected = agent.strip().lower()
    if selected not in {"claude", "codex"}:
        typer.echo("Error: agent must be 'claude' or 'codex'.", err=True)
        raise typer.Exit(1)
    try:
        repo = _parallelizer_repo_root(Path.cwd())
    except ParallelizerError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if not shutil.which(selected):
        typer.echo(f"Error: '{selected}' was not found on PATH.", err=True)
        raise typer.Exit(1)
    launch = ["uv", "run", "--project", str(repo), "python", str(repo / "mcp_server.py")]
    if selected == "codex":
        command = ["codex", "mcp", "add", "parallelizer", "--", *launch]
    else:
        command = ["claude", "mcp", "add", "--scope", scope, "--transport", "stdio", "parallelizer", "--", *launch]
    result = subprocess.run(command, check=False)
    raise typer.Exit(result.returncode)


def _parallelizer_repo_root(cwd: Path) -> Path:
    root = repo_root(cwd)
    pyproject = root / "pyproject.toml"
    is_parallelizer = (
        (root / "mcp_server.py").exists()
        and pyproject.exists()
        and 'name = "parallelizer"' in pyproject.read_text(encoding="utf-8")
    )
    if not is_parallelizer:
        raise ParallelizerError("plr mcp add must be run from inside the parallelizer repository.")
    return root


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


def _resolve_cd_target_path(service: ParallelizerService) -> str:
    records = [
        SimpleNamespace(name="main", status="source", worktree_path=str(service.project.root)),
        *service.list_records(),
    ]
    if not sys.stdin.isatty():
        raise ParallelizerError("A worktree name is required when stdin is not interactive.")
    if shutil.which("fzf"):
        selected = _select_record_path_fzf(records)
        if selected:
            return selected
    return _select_record_path_numbered(records)


def _select_record_fzf(records: List[TreeRecord]) -> Optional[str]:
    rows = "\n".join(f"{record.name}\t{record.status}\t{record.worktree_path}" for record in records)
    result = subprocess.run(
        ["fzf", "--with-nth=1,2,3"],
        input=rows,
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.split("\t", 1)[0].strip()


def _select_record_path_fzf(records: List[TreeRecord]) -> Optional[str]:
    rows = "\n".join(f"{record.name}\t{record.status}\t{record.worktree_path}" for record in records)
    result = subprocess.run(
        ["fzf", "--with-nth=1,2,3"],
        input=rows,
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    parts = result.stdout.rstrip("\n").split("\t", 2)
    if len(parts) < 3:
        return None
    return parts[2].strip()


def _select_record_numbered(records: List[TreeRecord]) -> str:
    for index, record in enumerate(records, start=1):
        typer.echo(f"{index}. {record.name}\t{record.status}\t{record.worktree_path}", err=True)
    choice = typer.prompt("Select worktree", type=int, err=True)
    if choice < 1 or choice > len(records):
        raise ParallelizerError("Invalid worktree selection.")
    return records[choice - 1].name


def _select_record_path_numbered(records: List[TreeRecord]) -> str:
    for index, record in enumerate(records, start=1):
        typer.echo(f"{index}. {record.name}\t{record.status}\t{record.worktree_path}", err=True)
    choice = typer.prompt("Select worktree", type=int, err=True)
    if choice < 1 or choice > len(records):
        raise ParallelizerError("Invalid worktree selection.")
    return records[choice - 1].worktree_path


def _prompt_from_args(parts: List[str]) -> str:
    if parts:
        return " ".join(parts)
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def _prompt_from_optional_arg(prompt: Optional[str]) -> str:
    if prompt:
        return prompt
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def _edit_prompt(prompt: str) -> str:
    editor = os.environ.get("EDITOR")
    if not editor:
        raise ParallelizerError("$EDITOR is not set.")
    command = shlex.split(editor)
    if not command:
        raise ParallelizerError("$EDITOR is not set.")
    with tempfile.TemporaryDirectory() as temp_dir:
        prompt_path = Path(temp_dir) / "prompt.md"
        prompt_path.write_text(f"{prompt.rstrip()}\n" if prompt else "", encoding="utf-8")
        tty = None
        try:
            try:
                tty = builtins.open("/dev/tty", "r+")
            except OSError:
                pass
            kwargs = {"check": False}
            if tty is not None:
                kwargs.update({"stdin": tty, "stdout": tty, "stderr": tty})
            result = subprocess.run([*command, str(prompt_path)], **kwargs)
        finally:
            if tty is not None:
                tty.close()
        if result.returncode != 0:
            raise ParallelizerError(f"Editor exited with status {result.returncode}.")
        return prompt_path.read_text(encoding="utf-8").strip()


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
