from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Mapping

from .errors import ParallelizerError

DEFAULT_CONFIG: Dict[str, Any] = {
    "default_coding_agent": "codex",
    "worktree_root": "~/.parallelizer/worktrees",
    "agents": {
        "codex": {
            "interactive": ["codex", "--cd", "{worktree}", "{prompt}"],
            "background": ["codex", "exec", "--cd", "{worktree}", "{prompt}"],
        },
        "claude": {
            "interactive": ["claude", "{prompt}"],
            "background": ["claude", "-p", "{prompt}"],
        },
    },
}


def load_config(repo_root: Path) -> Dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    global_path = Path("~/.parallelizer/global_config.json").expanduser()
    local_path = repo_root / ".parallelizer" / "local_config.json"
    for path in (global_path, local_path):
        if path.exists():
            config = _deep_merge(config, _read_json(path))
    return config


def write_global_default_agent(agent: str) -> Path:
    if agent not in {"codex", "claude"}:
        raise ParallelizerError("Default coding agent must be either 'codex' or 'claude'.")
    path = Path("~/.parallelizer/global_config.json").expanduser()
    config: Dict[str, Any] = {}
    if path.exists():
        config = _read_json(path)
    config["default_coding_agent"] = agent
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)
    return path


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ParallelizerError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ParallelizerError(f"Config file must contain a JSON object: {path}")
    return data


def _deep_merge(base: Dict[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
