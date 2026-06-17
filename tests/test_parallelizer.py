from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from parallelizer import cli
from parallelizer.config import load_config, write_global_default_agent
from parallelizer.errors import ParallelizerError
from parallelizer.prompts import manager_prompt, plr_instructions_markdown, setup_plr_prompt
from parallelizer.service import ParallelizerService

runner = CliRunner()


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


def test_background_agent_persists_model_and_agent_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)

    service.create_subagent(
        "do fake work",
        name="gamma",
        agent="fake",
        background=True,
        agent_args=["--flag"],
    )
    _wait_for_done(service, "gamma")
    refreshed = service.worktree_info("gamma")

    assert refreshed["agent_args"] == ["--flag"]


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


def test_global_init_preserves_unknown_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    config_dir = home / ".parallelizer"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    config_file = config_dir / "global_config.json"
    config_file.write_text(json.dumps({"worktree_root": "/tmp/plr", "default_coding_agent": "codex"}))

    written = write_global_default_agent("claude")
    data = json.loads(written.read_text())

    assert data == {"default_coding_agent": "claude", "worktree_root": "/tmp/plr"}


def test_known_agent_command_inserts_model_and_agent_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    service = ParallelizerService(repo)

    command = service.agent_command(
        "codex",
        "interactive",
        repo,
        "do it",
        model="gpt-test",
        agent_args=["--search"],
    )

    assert command == ["codex", "--cd", str(repo), "--model", "gpt-test", "--search", "do it"]


def test_unknown_agent_rejects_model_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)

    with pytest.raises(ParallelizerError, match="only supported"):
        service.agent_command("fake", "interactive", repo, "do it", model="x")


def test_manager_and_setup_prompts_include_operational_instructions() -> None:
    manager = manager_prompt("ship feature", 7)
    setup = setup_plr_prompt("use port offsets")

    assert "plr sub <name>" in manager
    assert "Sleep for 7 seconds" in manager
    assert "ship feature" in manager
    assert "setup_environment" in setup
    assert "use port offsets" in setup


def test_instructions_markdown_documents_commands_and_setup() -> None:
    instructions = plr_instructions_markdown()

    assert "plr sub [name] [prompt]" in instructions
    assert "plr agent setup_plr" in instructions
    assert "setup_environment" in instructions
    assert "allocated worktree number as `$1`" in instructions


def test_instructions_command_prints_markdown() -> None:
    result = runner.invoke(cli.app, ["instructions"])

    assert result.exit_code == 0
    assert "## Parallelizer (`plr`)" in result.stdout
    assert "plr instructions" in result.stdout


def test_resolve_record_requires_name_when_not_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    record = SimpleNamespace(name="alpha", status="done", worktree_path="/tmp/alpha")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    with pytest.raises(ParallelizerError, match="required"):
        cli._resolve_record_name([record], None)


def test_resolve_record_uses_fzf(monkeypatch: pytest.MonkeyPatch) -> None:
    record = SimpleNamespace(name="alpha", status="done", worktree_path="/tmp/alpha")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/bin/fzf" if name == "fzf" else None)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="alpha\tdone\t/tmp/alpha\n"),
    )

    assert cli._resolve_record_name([record], None) == "alpha"


def test_open_tmux_runs_split_window(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    service = SimpleNamespace(
        list_records=lambda: [SimpleNamespace(name="alpha", status="done", worktree_path="/tmp/alpha")],
        worktree_info=lambda name: {"worktree_path": "/tmp/alpha"},
    )
    monkeypatch.setenv("TMUX", "tmux-session")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/opt/bin/tmux" if name == "tmux" else None)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0, stderr="", stdout=""),
    )

    cli._open_tmux(service, "alpha")

    assert calls == [["tmux", "split-window", "-c", "/tmp/alpha"]]


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
