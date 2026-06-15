from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from parallelizer.config import load_config
from parallelizer.errors import ParallelizerError
from parallelizer.service import ParallelizerService


def test_config_merges_global_and_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home_config = home / ".parallelizer"
    repo_config = repo / ".parallelizer"
    home_config.mkdir(parents=True)
    repo_config.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (home_config / "global_config.json").write_text(
        json.dumps({"default_coding_agent": "claude", "agents": {"fake": {"background": ["x"]}}})
    )
    (repo_config / "local_config.json").write_text(json.dumps({"default_coding_agent": "fake"}))

    config = load_config(repo)

    assert config["default_coding_agent"] == "fake"
    assert config["agents"]["codex"]["background"][0] == "codex"
    assert config["agents"]["fake"]["background"] == ["x"]


def test_create_tree_runs_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")

    record = ParallelizerService(repo).create_tree(name="alpha", prompt="hello")

    worktree = Path(record.worktree_path)
    assert worktree.exists()
    assert (worktree / "setup-number.txt").read_text().strip() == "1"
    assert record.setup_status == "done"


def test_background_agent_updates_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)

    record = service.create_subagent("do fake work", name="beta", agent="fake", background=True)
    _wait_for_done(service, "beta")
    refreshed = service.worktree_info("beta")

    assert record.pid
    assert refreshed["status"] == "done"
    assert refreshed["exit_code"] == 0


def test_setup_missing_function_preserves_error_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path, setup_body="not_the_right_name() { true; }\n")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)

    with pytest.raises(ParallelizerError, match="setup_environment"):
        service.create_tree(name="broken")

    record = service.worktree_info("broken")
    assert record["status"] == "error"
    assert record["setup_status"] == "error"
    assert Path(record["worktree_path"]).exists()


def _init_repo(tmp_path: Path, setup_body: str | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], repo)
    (repo / "README.md").write_text("test repo\n")
    setup_dir = repo / ".parallelizer"
    setup_dir.mkdir()
    body = setup_body or "setup_environment() { echo $1 > setup-number.txt; }\n"
    (setup_dir / "functions.sh").write_text(body)
    _run(["git", "add", "."], repo)
    _run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"], repo)
    return repo


def _write_config(repo: Path, worktree_root: Path) -> None:
    config = {
        "default_coding_agent": "fake",
        "worktree_root": str(worktree_root),
        "agents": {
            "fake": {
                "interactive": [sys.executable, "-c", "print('interactive fake')"],
                "background": [sys.executable, "-c", "print('agent ok')"],
            }
        },
    }
    (repo / ".parallelizer" / "local_config.json").write_text(json.dumps(config))


def _wait_for_done(service: ParallelizerService, name: str) -> None:
    for _ in range(30):
        info = service.worktree_info(name)
        if info["status"] in {"done", "error"}:
            return
        time.sleep(0.1)
    raise AssertionError("background agent did not finish")


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True)
