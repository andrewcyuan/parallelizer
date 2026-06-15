from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Project:
    root: Path
    slug: str
    state_file: Path
    worktree_root: Path


@dataclass
class TreeRecord:
    name: str
    source_repo: str
    worktree_path: str
    branch: str
    allocation_number: int
    prompt_summary: str = ""
    agent: Optional[str] = None
    mode: Optional[str] = None
    pid: Optional[int] = None
    log_path: Optional[str] = None
    status: str = "no-agent"
    setup_status: str = "skipped"
    setup_error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    exit_code: Optional[int] = None
    model: Optional[str] = None
    agent_args: Optional[List[str]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TreeRecord":
        data = {**data}
        data.setdefault("model", None)
        data.setdefault("agent_args", None)
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "source_repo": self.source_repo,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "allocation_number": self.allocation_number,
            "prompt_summary": self.prompt_summary,
            "agent": self.agent,
            "mode": self.mode,
            "pid": self.pid,
            "log_path": self.log_path,
            "status": self.status,
            "setup_status": self.setup_status,
            "setup_error": self.setup_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "exit_code": self.exit_code,
            "model": self.model,
            "agent_args": self.agent_args,
        }
