from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import load_config
from .errors import ParallelizerError
from .git_utils import add_worktree, merge_branch, remove_worktree, repo_root, worktree_porcelain
from .models import Project, TreeRecord
from .state import StateStore


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ParallelizerService:
    def __init__(self, cwd: Path):
        root = repo_root(cwd)
        self.config = load_config(root)
        self.project = self._project(root)
        self.state = StateStore(self.project.state_file)

    def create_tree(self, name: Optional[str] = None, prompt: str = "") -> TreeRecord:
        number = self.state.allocate_number()
        tree_name = self._next_name(name)
        worktree_path = self.project.worktree_root / self.project.slug / tree_name
        branch = f"plr/{tree_name}"
        if worktree_path.exists():
            raise ParallelizerError(f"Worktree path already exists: {worktree_path}")
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        add_worktree(self.project.root, worktree_path, branch)
        record = self._new_record(tree_name, worktree_path, branch, number, prompt)
        try:
            self._run_setup(record)
        except ParallelizerError:
            record.status = "error"
            record.updated_at = utc_now()
            self.state.put(record)
            raise
        self.state.put(record)
        return record

    def create_subagent(
        self,
        prompt: str,
        name: Optional[str] = None,
        agent: Optional[str] = None,
        background: bool = False,
        model: Optional[str] = None,
        agent_args: Optional[List[str]] = None,
    ) -> TreeRecord:
        if not prompt.strip():
            raise ParallelizerError("A prompt is required to start a subagent.")
        record = self.create_tree(name=name, prompt=prompt)
        selected_agent = agent or str(self.config.get("default_coding_agent", "codex"))
        if background:
            self.start_background_agent(record, selected_agent, prompt, model, agent_args)
        else:
            record.agent = selected_agent
            record.mode = "interactive"
            record.model = model
            record.agent_args = agent_args or []
            record.updated_at = utc_now()
            self.state.put(record)
        return record

    def start_background_agent(
        self,
        record: TreeRecord,
        agent: str,
        prompt: str,
        model: Optional[str] = None,
        agent_args: Optional[List[str]] = None,
    ) -> None:
        command = self.agent_command(
            agent,
            "background",
            Path(record.worktree_path),
            prompt,
            model,
            agent_args,
            hook_record_name=record.name,
            hook_state_file=self.project.state_file,
        )
        log_path = Path("~/.parallelizer/logs").expanduser() / self.project.slug / f"{record.name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        runner_command = [
            sys.executable,
            "-m",
            "parallelizer.runner",
            "--state-file",
            str(self.project.state_file),
            "--name",
            record.name,
            "--worktree",
            record.worktree_path,
            "--log-path",
            str(log_path),
            "--command-json",
            json.dumps(command),
        ]
        proc = subprocess.Popen(
            runner_command,
            cwd=record.worktree_path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        record.agent = agent
        record.mode = "background"
        record.pid = proc.pid
        record.log_path = str(log_path)
        record.status = "running"
        record.model = model
        record.agent_args = agent_args or []
        record.updated_at = utc_now()
        self.state.put(record)

    def interactive_command(self, record: TreeRecord) -> List[str]:
        if not record.agent:
            raise ParallelizerError("Record has no agent configured.")
        return self.agent_command(
            record.agent,
            "interactive",
            Path(record.worktree_path),
            record.prompt_summary,
            record.model,
            record.agent_args,
        )

    def current_repo_agent_command(
        self,
        prompt: str,
        agent: Optional[str] = None,
        model: Optional[str] = None,
        agent_args: Optional[List[str]] = None,
    ) -> List[str]:
        selected_agent = agent or str(self.config.get("default_coding_agent", "codex"))
        return self.agent_command(selected_agent, "interactive", self.project.root, prompt, model, agent_args)

    def agent_command(
        self,
        agent: str,
        mode: str,
        worktree: Path,
        prompt: str,
        model: Optional[str] = None,
        agent_args: Optional[List[str]] = None,
        hook_record_name: Optional[str] = None,
        hook_state_file: Optional[Path] = None,
    ) -> List[str]:
        agents = self.config.get("agents", {})
        template = agents.get(agent, {}).get(mode)
        if not template:
            raise ParallelizerError(f"No {mode} command template configured for agent: {agent}")
        extras = self._agent_extras(
            agent,
            mode,
            model,
            agent_args or [],
            hook_record_name=hook_record_name,
            hook_state_file=hook_state_file,
        )
        return self._format_agent_command(template, worktree, prompt, extras)

    def list_records(self) -> List[TreeRecord]:
        git_paths = self._git_worktree_paths()
        records = [self._refresh_status(record, git_paths) for record in self.state.records()]
        for record in records:
            self.state.put(record)
        return sorted(records, key=lambda item: item.created_at)

    def worktree_info(self, name: str) -> Dict[str, Any]:
        record = self.state.get(name)
        if not record:
            raise ParallelizerError(f"Unknown worktree: {name}")
        refreshed = self._refresh_status(record, self._git_worktree_paths())
        self.state.put(refreshed)
        return {
            **refreshed.to_dict(),
            "cd_command": f"cd {refreshed.worktree_path}",
        }

    def remove_tree(self, name: str, force_cleanup: bool = False) -> TreeRecord:
        record = self._record_for_action(name)
        self._ensure_not_running(record)
        self._run_cleanup(record, force_cleanup)
        remove_worktree(self.project.root, Path(record.worktree_path))
        self.state.delete(record.name)
        return record

    def merge_tree(
        self,
        name: str,
        no_ff: bool = False,
        squash: bool = False,
        force_cleanup: bool = False,
    ) -> TreeRecord:
        if no_ff and squash:
            raise ParallelizerError("--no-ff and --squash cannot be used together.")
        record = self._record_for_action(name)
        self._ensure_not_running(record)
        merge_branch(self.project.root, record.branch, no_ff=no_ff, squash=squash)
        return self.remove_tree(name, force_cleanup=force_cleanup)

    def run_in_worktree(self, name: str, command: List[str]) -> Dict[str, Any]:
        if not command:
            raise ParallelizerError("A command is required to run in a worktree.")
        record = self._record_for_action(name)
        result = subprocess.run(
            command,
            cwd=record.worktree_path,
            text=True,
            capture_output=True,
            check=False,
        )
        return {
            "name": record.name,
            "worktree_path": record.worktree_path,
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def _project(self, root: Path) -> Project:
        digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:8]
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", root.name).strip("-") or "repo"
        slug = f"{safe_name}-{digest}"
        worktree_root = Path(str(self.config["worktree_root"])).expanduser()
        state_file = Path("~/.parallelizer/state").expanduser() / f"{slug}.json"
        return Project(root=root, slug=slug, state_file=state_file, worktree_root=worktree_root)

    def _next_name(self, requested: Optional[str]) -> str:
        existing = {record.name for record in self.state.records()}
        if requested:
            name = self._sanitize_name(requested)
            if name in existing:
                raise ParallelizerError(f"Worktree name already exists: {name}")
            return name
        index = 1
        while f"worker-{index}" in existing:
            index += 1
        return f"worker-{index}"

    def _sanitize_name(self, value: str) -> str:
        name = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
        if not name:
            raise ParallelizerError("Worktree name cannot be empty.")
        return name

    def _new_record(
        self,
        name: str,
        path: Path,
        branch: str,
        number: int,
        prompt: str,
    ) -> TreeRecord:
        now = utc_now()
        return TreeRecord(
            name=name,
            source_repo=str(self.project.root),
            worktree_path=str(path),
            branch=branch,
            allocation_number=number,
            prompt_summary=prompt,
            created_at=now,
            updated_at=now,
        )

    def _run_setup(self, record: TreeRecord) -> None:
        setup_file = Path(record.worktree_path) / ".parallelizer" / "functions.sh"
        if not setup_file.exists():
            record.setup_status = "skipped"
            return
        script = (
            "source .parallelizer/functions.sh; "
            "if ! declare -F setup_environment >/dev/null; then "
            "echo 'setup_environment function not found in .parallelizer/functions.sh' >&2; "
            "exit 127; "
            "fi; "
            "setup_environment \"$1\""
        )
        result = subprocess.run(
            ["bash", "-lc", script, "parallelizer-setup", str(record.allocation_number)],
            cwd=record.worktree_path,
            env=self._hook_env(record),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            record.setup_status = "error"
            record.setup_error = (result.stderr or result.stdout).strip()
            raise ParallelizerError(f"Setup failed for {record.name}: {record.setup_error}")
        record.setup_status = "done"

    def _run_cleanup(self, record: TreeRecord, force_cleanup: bool) -> None:
        setup_file = Path(record.worktree_path) / ".parallelizer" / "functions.sh"
        if not setup_file.exists():
            return
        script = (
            "source .parallelizer/functions.sh; "
            "if ! declare -F cleanup_environment >/dev/null; then "
            "exit 0; "
            "fi; "
            "cleanup_environment \"$1\""
        )
        result = subprocess.run(
            ["bash", "-lc", script, "parallelizer-cleanup", str(record.allocation_number)],
            cwd=record.worktree_path,
            env=self._hook_env(record),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 and not force_cleanup:
            message = (result.stderr or result.stdout).strip()
            raise ParallelizerError(f"Cleanup failed for {record.name}: {message}")

    def _hook_env(self, record: TreeRecord) -> Dict[str, str]:
        env = os.environ.copy()
        env["PLR_SOURCE_REPO"] = record.source_repo
        env["PLR_WORKTREE"] = record.worktree_path
        return env

    def _record_for_action(self, name: str) -> TreeRecord:
        record = self.state.get(name)
        if not record:
            raise ParallelizerError(f"Unknown worktree: {name}")
        refreshed = self._refresh_status(record, self._git_worktree_paths())
        self.state.put(refreshed)
        return refreshed

    def _ensure_not_running(self, record: TreeRecord) -> None:
        if record.pid and record.exit_code is None and self._pid_alive(record.pid):
            record.status = "awaiting-permission" if record.pending_permission else "running"
            record.updated_at = utc_now()
            self.state.put(record)
            raise ParallelizerError(f"Cannot remove {record.name}: background agent is still running.")

    def _agent_extras(
        self,
        agent: str,
        mode: str,
        model: Optional[str],
        agent_args: List[str],
        hook_record_name: Optional[str] = None,
        hook_state_file: Optional[Path] = None,
    ) -> List[str]:
        extras: List[str] = []
        if model:
            if agent in {"codex", "claude"}:
                extras.extend(["--model", model])
            else:
                raise ParallelizerError("--model is only supported for codex and claude agents.")
        if mode == "background" and hook_record_name and hook_state_file:
            extras.extend(self._agent_hook_args(agent, hook_record_name, hook_state_file))
        extras.extend(agent_args)
        return extras

    def _agent_hook_args(self, agent: str, record_name: str, state_file: Path) -> List[str]:
        if agent == "codex":
            return self._codex_hook_args(record_name, state_file)
        if agent == "claude":
            return self._claude_hook_args(record_name, state_file)
        return []

    def _codex_hook_args(self, record_name: str, state_file: Path) -> List[str]:
        permission_command = self._hook_command(record_name, "permission-request", state_file)
        clear_command = self._hook_command(record_name, "clear-permission", state_file)
        hooks = {
            "hooks.PermissionRequest": permission_command,
            "hooks.PreToolUse": clear_command,
            "hooks.PostToolUse": clear_command,
            "hooks.Stop": clear_command,
        }
        args: List[str] = []
        for key, command in hooks.items():
            args.extend(["-c", f"{key}=[{{ command = {json.dumps(command)} }}]"])
        return args

    def _claude_hook_args(self, record_name: str, state_file: Path) -> List[str]:
        permission_command = self._hook_command(record_name, "permission-request", state_file)
        clear_command = self._hook_command(record_name, "clear-permission", state_file)
        settings = {
            "hooks": {
                "PermissionRequest": [{"hooks": [{"type": "command", "command": permission_command}]}],
                "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": clear_command}]}],
                "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": clear_command}]}],
                "Stop": [{"hooks": [{"type": "command", "command": clear_command}]}],
            }
        }
        return ["--settings", json.dumps(settings, sort_keys=True)]

    def _hook_command(self, record_name: str, event: str, state_file: Path) -> str:
        return shlex.join(
            [
                sys.executable,
                "-m",
                "parallelizer.agent_event",
                record_name,
                event,
                "--state-file",
                str(state_file),
            ]
        )

    def _format_agent_command(
        self,
        template: List[str],
        worktree: Path,
        prompt: str,
        extras: List[str],
    ) -> List[str]:
        prompt_index = self._prompt_template_index(template)
        formatted = [str(part).format(worktree=str(worktree), prompt=prompt) for part in template]
        if prompt_index is None:
            return [*formatted, *extras, prompt]
        return [*formatted[:prompt_index], *extras, *formatted[prompt_index:]]

    def _prompt_template_index(self, template: List[str]) -> Optional[int]:
        for index, part in enumerate(template):
            if "{prompt}" in str(part):
                return index
        return None

    def _git_worktree_paths(self) -> set[str]:
        paths: set[str] = set()
        for line in worktree_porcelain(self.project.root).splitlines():
            if line.startswith("worktree "):
                paths.add(line.removeprefix("worktree "))
        return paths

    def _refresh_status(self, record: TreeRecord, git_paths: set[str]) -> TreeRecord:
        if record.worktree_path not in git_paths and not Path(record.worktree_path).exists():
            record.status = "missing"
        elif record.exit_code is not None:
            if record.exit_code != 0 and record.pending_permission:
                record.status = "permission-required"
                record.updated_at = utc_now()
                return record
            record.status = "done" if record.exit_code == 0 else "error"
        elif record.pid and self._pid_alive(record.pid):
            record.status = "awaiting-permission" if record.pending_permission else "running"
        elif record.pid:
            record.status = "error"
        elif record.setup_status == "error":
            record.status = "error"
        else:
            record.status = "no-agent"
        record.updated_at = utc_now()
        return record

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
