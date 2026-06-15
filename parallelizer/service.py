from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import load_config
from .errors import ParallelizerError
from .git_utils import add_worktree, repo_root, worktree_porcelain
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
        command = self.agent_command(agent, "background", Path(record.worktree_path), prompt, model, agent_args)
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
    ) -> List[str]:
        agents = self.config.get("agents", {})
        template = agents.get(agent, {}).get(mode)
        if not template:
            raise ParallelizerError(f"No {mode} command template configured for agent: {agent}")
        extras = self._agent_extras(agent, model, agent_args or [])
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
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            record.setup_status = "error"
            record.setup_error = (result.stderr or result.stdout).strip()
            raise ParallelizerError(f"Setup failed for {record.name}: {record.setup_error}")
        record.setup_status = "done"

    def _agent_extras(self, agent: str, model: Optional[str], agent_args: List[str]) -> List[str]:
        extras: List[str] = []
        if model:
            if agent in {"codex", "claude"}:
                extras.extend(["--model", model])
            else:
                raise ParallelizerError("--model is only supported for codex and claude agents.")
        extras.extend(agent_args)
        return extras

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
            record.status = "done" if record.exit_code == 0 else "error"
        elif record.pid and self._pid_alive(record.pid):
            record.status = "running"
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
