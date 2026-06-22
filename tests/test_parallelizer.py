from __future__ import annotations

import json
import os
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
from parallelizer.models import TreeRecord
from parallelizer.prompts import manager_prompt, plr_instructions_markdown, setup_plr_prompt
from parallelizer.service import ParallelizerService
from parallelizer.state import StateStore

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


def test_service_uses_source_repo_state_when_started_inside_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    record = ParallelizerService(repo).create_tree(name="alpha", prompt="hello")

    service = ParallelizerService(Path(record.worktree_path))

    assert service.project.root == repo.resolve()
    assert [item.name for item in service.list_records()] == ["alpha"]
    assert service.worktree_info("alpha")["worktree_path"] == record.worktree_path


def test_setup_receives_source_repo_and_worktree_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(
        tmp_path,
        setup_body=(
            "setup_environment() {\n"
            "  if [ -f \"$PLR_SOURCE_REPO/.env\" ] && [ ! -f \".env\" ]; then\n"
            "    cp \"$PLR_SOURCE_REPO/.env\" \".env\"\n"
            "  fi\n"
            "  printf '%s' \"$PLR_SOURCE_REPO\" > source-repo.txt\n"
            "  printf '%s' \"$PLR_WORKTREE\" > worktree-path.txt\n"
            "  printf '%s' \"$1\" > setup-number.txt\n"
            "}\n"
        ),
    )
    (repo / ".env").write_text("TOKEN=source\n")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")

    record = ParallelizerService(repo).create_tree(name="alpha", prompt="hello")

    worktree = Path(record.worktree_path)
    assert (worktree / ".env").read_text() == "TOKEN=source\n"
    assert (worktree / "source-repo.txt").read_text() == str(repo)
    assert (worktree / "worktree-path.txt").read_text() == record.worktree_path
    assert (worktree / "setup-number.txt").read_text() == "1"


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


def test_agent_event_permission_request_stores_bounded_summary(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    record = _record("alpha", tmp_path / "alpha")
    record.agent = "codex"
    StateStore(state_file).put(record)
    long_summary = "x" * 400

    result = runner.invoke(
        cli.app,
        ["agent", "event", "alpha", "permission-request", "--state-file", str(state_file)],
        input=json.dumps({"tool_name": "Bash", "summary": long_summary, "command": "rm -rf build"}),
    )

    assert result.exit_code == 0
    stored = StateStore(state_file).get("alpha")
    assert stored is not None
    assert stored.pending_permission is not None
    assert stored.pending_permission["agent"] == "codex"
    assert stored.pending_permission["tool"] == "Bash"
    assert len(stored.pending_permission["summary"]) <= 240
    assert stored.pending_permission["payload"]["command"] == "rm -rf build"
    assert stored.pending_permission["requested_at"]


def test_agent_event_clear_removes_pending_permission(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    record = _record("alpha", tmp_path / "alpha")
    record.pending_permission = {"agent": "codex", "tool": "Bash", "summary": "needs permission"}
    StateStore(state_file).put(record)

    result = runner.invoke(
        cli.app,
        ["agent", "event", "alpha", "clear-permission", "--state-file", str(state_file)],
        input=json.dumps({"tool_name": "Bash"}),
    )

    assert result.exit_code == 0
    stored = StateStore(state_file).get("alpha")
    assert stored is not None
    assert stored.pending_permission is None


def test_agent_event_bad_or_empty_stdin_does_not_crash(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    StateStore(state_file).put(_record("alpha", tmp_path / "alpha"))

    bad = runner.invoke(
        cli.app,
        ["agent", "event", "alpha", "permission-request", "--state-file", str(state_file)],
        input="{not json",
    )
    empty = runner.invoke(
        cli.app,
        ["agent", "event", "alpha", "clear-permission", "--state-file", str(state_file)],
        input="",
    )

    assert bad.exit_code == 0
    assert empty.exit_code == 0


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


def test_status_uses_pending_permission_for_running_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)
    record = service.create_tree(name="alpha")
    record.mode = "background"
    record.agent = "codex"
    record.pid = os.getpid()
    record.pending_permission = {"agent": "codex", "tool": "Bash", "summary": "needs permission"}
    service.state.put(record)

    info = service.worktree_info("alpha")

    assert info["status"] == "awaiting-permission"


def test_status_returns_running_after_permission_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)
    record = service.create_tree(name="alpha")
    record.mode = "background"
    record.agent = "codex"
    record.pid = os.getpid()
    record.pending_permission = None
    service.state.put(record)

    info = service.worktree_info("alpha")

    assert info["status"] == "running"


def test_status_reports_permission_required_for_failed_agent_with_pending_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)
    record = service.create_tree(name="alpha")
    record.mode = "background"
    record.agent = "codex"
    record.pid = os.getpid()
    record.exit_code = 1
    record.pending_permission = {"agent": "codex", "tool": "Bash", "summary": "needs permission"}
    service.state.put(record)

    info = service.worktree_info("alpha")

    assert info["status"] == "permission-required"


def test_remove_tree_runs_cleanup_and_deletes_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / "cleanup-number.txt"
    source_marker = tmp_path / "cleanup-source-repo.txt"
    worktree_marker = tmp_path / "cleanup-worktree.txt"
    repo = _init_repo(
        tmp_path,
        setup_body=(
            "setup_environment() { true; }\n"
            "cleanup_environment() {\n"
            f"  printf '%s' \"$1\" > \"{marker}\"\n"
            f"  printf '%s' \"$PLR_SOURCE_REPO\" > \"{source_marker}\"\n"
            f"  printf '%s' \"$PLR_WORKTREE\" > \"{worktree_marker}\"\n"
            "}\n"
        ),
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)
    record = service.create_tree(name="alpha")

    removed = service.remove_tree("alpha")

    assert removed.name == "alpha"
    assert marker.read_text() == "1"
    assert source_marker.read_text() == str(repo)
    assert worktree_marker.read_text() == record.worktree_path
    assert not Path(record.worktree_path).exists()
    assert service.state.get("alpha") is None


def test_remove_tree_skips_missing_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path, setup_body="setup_environment() { true; }\n")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)
    record = service.create_tree(name="alpha")

    service.remove_tree("alpha")

    assert not Path(record.worktree_path).exists()
    assert service.state.get("alpha") is None


def test_remove_tree_cleanup_failure_preserves_worktree_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(
        tmp_path,
        setup_body=(
            "setup_environment() { true; }\n"
            "cleanup_environment() { echo cleanup nope >&2; return 42; }\n"
        ),
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)
    record = service.create_tree(name="alpha")

    with pytest.raises(ParallelizerError, match="cleanup nope"):
        service.remove_tree("alpha")

    assert Path(record.worktree_path).exists()
    assert service.state.get("alpha") is not None


def test_remove_tree_force_continues_after_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(
        tmp_path,
        setup_body=(
            "setup_environment() { true; }\n"
            "cleanup_environment() { echo cleanup nope >&2; return 42; }\n"
        ),
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)
    record = service.create_tree(name="alpha")

    service.remove_tree("alpha", force_cleanup=True)

    assert not Path(record.worktree_path).exists()
    assert service.state.get("alpha") is None


def test_remove_tree_force_does_not_bypass_dirty_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(
        tmp_path,
        setup_body=(
            "setup_environment() { echo dirty > dirty.txt; }\n"
            "cleanup_environment() { echo cleanup nope >&2; return 42; }\n"
        ),
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)
    record = service.create_tree(name="alpha")

    with pytest.raises(ParallelizerError, match="Unable to remove git worktree"):
        service.remove_tree("alpha", force_cleanup=True)

    assert Path(record.worktree_path).exists()
    assert service.state.get("alpha") is not None


def test_remove_tree_refuses_running_background_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path, setup_body="setup_environment() { true; }\n")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)
    record = service.create_tree(name="alpha")
    record.pid = os.getpid()
    record.mode = "background"
    service.state.put(record)

    with pytest.raises(ParallelizerError, match="still running"):
        service.remove_tree("alpha")

    assert Path(record.worktree_path).exists()
    assert service.state.get("alpha") is not None


def test_merge_tree_merges_branch_then_removes_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "cleanup-number.txt"
    repo = _init_repo(
        tmp_path,
        setup_body=(
            "setup_environment() { true; }\n"
            f"cleanup_environment() {{ printf '%s' \"$1\" > \"{marker}\"; }}\n"
        ),
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)
    record = service.create_tree(name="feature")
    worktree = Path(record.worktree_path)
    (worktree / "feature.txt").write_text("feature\n")
    _run(["git", "add", "feature.txt"], worktree)
    _commit(worktree, "feature")

    service.merge_tree("feature")

    assert (repo / "feature.txt").read_text() == "feature\n"
    assert marker.read_text() == "1"
    assert not worktree.exists()
    assert service.state.get("feature") is None
    _run(["git", "rev-parse", "--verify", "plr/feature"], repo)


def test_merge_tree_conflict_preserves_worktree_state_and_prints_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "cleanup-number.txt"
    repo = _init_repo(
        tmp_path,
        setup_body=(
            "setup_environment() { true; }\n"
            f"cleanup_environment() {{ printf '%s' \"$1\" > \"{marker}\"; }}\n"
        ),
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)
    record = service.create_tree(name="conflict")
    worktree = Path(record.worktree_path)
    (worktree / "README.md").write_text("worktree\n")
    _run(["git", "add", "README.md"], worktree)
    _commit(worktree, "worktree edit")
    (repo / "README.md").write_text("main\n")
    _run(["git", "add", "README.md"], repo)
    _commit(repo, "main edit")

    with pytest.raises(ParallelizerError, match="Unable to merge plr/conflict") as exc_info:
        service.merge_tree("conflict")

    assert "CONFLICT" in str(exc_info.value)
    assert not marker.exists()
    assert worktree.exists()
    assert service.state.get("conflict") is not None
    _run(["git", "merge", "--abort"], repo)


def test_merge_tree_rejects_mutually_exclusive_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path, setup_body="setup_environment() { true; }\n")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    service = ParallelizerService(repo)

    with pytest.raises(ParallelizerError, match="cannot be used together"):
        service.merge_tree("feature", no_ff=True, squash=True)


def test_cli_merge_passes_merge_flags() -> None:
    calls = {}
    service = SimpleNamespace(
        merge_tree=lambda name, no_ff, squash, force_cleanup: calls.update(
            {"name": name, "no_ff": no_ff, "squash": squash, "force_cleanup": force_cleanup}
        )
        or SimpleNamespace(name=name, branch="plr/alpha", worktree_path="/tmp/alpha")
    )

    cli._merge(service, "alpha", no_ff=True, squash=False, force=True)

    assert calls == {"name": "alpha", "no_ff": True, "squash": False, "force_cleanup": True}


def test_cli_remove_all_removes_every_record() -> None:
    calls = []
    records = [
        SimpleNamespace(name="alpha", worktree_path="/tmp/alpha"),
        SimpleNamespace(name="beta", worktree_path="/tmp/beta"),
    ]
    service = SimpleNamespace(
        list_records=lambda: records,
        remove_tree=lambda name, force_cleanup: calls.append((name, force_cleanup))
        or SimpleNamespace(name=name, worktree_path=f"/tmp/{name}"),
    )

    cli._remove(service, ["*"], force=True)

    assert calls == [("alpha", True), ("beta", True)]


def test_cli_remove_without_name_uses_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    records = [
        SimpleNamespace(name="alpha", status="done", worktree_path="/tmp/alpha"),
        SimpleNamespace(name="beta", status="done", worktree_path="/tmp/beta"),
    ]
    service = SimpleNamespace(
        list_records=lambda: records,
        remove_tree=lambda name, force_cleanup: calls.append((name, force_cleanup))
        or SimpleNamespace(name=name, worktree_path=f"/tmp/{name}"),
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli.typer, "prompt", lambda *args, **kwargs: 2)

    cli._remove(service, [], force=False)

    assert calls == [("beta", False)]


def test_remove_alias_removes_all_records(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    records = [
        SimpleNamespace(name="alpha", worktree_path="/tmp/alpha"),
        SimpleNamespace(name="beta", worktree_path="/tmp/beta"),
    ]
    service = SimpleNamespace(
        list_records=lambda: records,
        remove_tree=lambda name, force_cleanup: calls.append((name, force_cleanup))
        or SimpleNamespace(name=name, worktree_path=f"/tmp/{name}"),
    )
    monkeypatch.setattr(cli, "ParallelizerService", lambda path: service)

    result = runner.invoke(cli.app, ["remove", "*", "--force"])

    assert result.exit_code == 0
    assert calls == [("alpha", True), ("beta", True)]


def test_cli_remove_rejects_multiple_names() -> None:
    service = SimpleNamespace()

    with pytest.raises(ParallelizerError, match="Only one worktree name"):
        cli._remove(service, ["alpha", "beta"], force=False)


def test_wt_runs_command_in_resolved_worktree(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    service = SimpleNamespace(worktree_info=lambda name: {"worktree_path": f"/tmp/{name}"})
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0),
    )

    with pytest.raises(cli.typer.Exit) as exc:
        cli._wt(service, "alpha", ["git", "status", "--short"])

    assert exc.value.exit_code == 0
    assert calls == [(["git", "status", "--short"], {"cwd": "/tmp/alpha", "check": False})]


def test_wt_cli_passes_flags_after_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    service = SimpleNamespace(worktree_info=lambda name: {"worktree_path": "/tmp/alpha"})
    monkeypatch.setattr(cli, "ParallelizerService", lambda path: service)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0),
    )

    result = runner.invoke(cli.app, ["wt", "alpha", "--", "git", "commit", "-m", "msg", "--short"])

    assert result.exit_code == 0
    assert calls == [(["git", "commit", "-m", "msg", "--short"], {"cwd": "/tmp/alpha", "check": False})]


def test_wt_cli_propagates_child_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SimpleNamespace(worktree_info=lambda name: {"worktree_path": "/tmp/alpha"})
    monkeypatch.setattr(cli, "ParallelizerService", lambda path: service)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(returncode=17),
    )

    result = runner.invoke(cli.app, ["wt", "alpha", "--", "git", "diff", "--check"])

    assert result.exit_code == 17


def test_wt_cli_requires_command_after_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SimpleNamespace(worktree_info=lambda name: {"worktree_path": "/tmp/alpha"})
    monkeypatch.setattr(cli, "ParallelizerService", lambda path: service)

    result = runner.invoke(cli.app, ["wt", "alpha", "--"])

    assert result.exit_code == 1
    assert "A command is required after --" in result.stderr


def test_wt_cli_surfaces_unknown_worktree(monkeypatch: pytest.MonkeyPatch) -> None:
    def worktree_info(name: str) -> dict:
        raise ParallelizerError(f"Unknown worktree: {name}")

    service = SimpleNamespace(worktree_info=worktree_info)
    monkeypatch.setattr(cli, "ParallelizerService", lambda path: service)

    result = runner.invoke(cli.app, ["wt", "missing", "--", "git", "status"])

    assert result.exit_code == 1
    assert "Unknown worktree: missing" in result.stderr


def test_run_in_worktree_captures_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)
    record = service.create_tree(name="alpha")
    # sentinel only exists in the worktree, proving the command ran there.
    (Path(record.worktree_path) / "sentinel.txt").write_text("hello\n")

    result = service.run_in_worktree("alpha", ["cat", "sentinel.txt"])

    assert result["exit_code"] == 0
    assert result["stdout"] == "hello\n"
    assert result["name"] == "alpha"
    assert result["worktree_path"] == record.worktree_path
    assert result["command"] == ["cat", "sentinel.txt"]


def test_run_in_worktree_reports_nonzero_exit_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)
    service.create_tree(name="alpha")

    result = service.run_in_worktree("alpha", ["sh", "-c", "echo oops >&2; exit 3"])

    assert result["exit_code"] == 3
    assert result["stderr"].strip() == "oops"


def test_run_in_worktree_requires_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)
    service.create_tree(name="alpha")

    with pytest.raises(ParallelizerError, match="command is required"):
        service.run_in_worktree("alpha", [])


def test_run_in_worktree_rejects_unknown_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)

    with pytest.raises(ParallelizerError, match="Unknown worktree"):
        service.run_in_worktree("missing", ["git", "status"])


def test_parallelizer_repo_root_accepts_repo_and_subdir(tmp_path: Path) -> None:
    repo = _init_parallelizer_repo(tmp_path)
    subdir = repo / "scripts"
    subdir.mkdir()

    assert cli._parallelizer_repo_root(repo) == repo.resolve()
    assert cli._parallelizer_repo_root(subdir) == repo.resolve()


def test_parallelizer_repo_root_rejects_other_git_repo(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    with pytest.raises(ParallelizerError, match="parallelizer repository"):
        cli._parallelizer_repo_root(repo)


def test_parallelizer_repo_root_rejects_non_git_dir(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(ParallelizerError, match="git repository"):
        cli._parallelizer_repo_root(plain)


def test_mcp_add_builds_codex_command(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(cli, "_parallelizer_repo_root", lambda cwd: Path("/repo"))
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0),
    )

    result = runner.invoke(cli.app, ["mcp", "add", "codex"])

    assert result.exit_code == 0
    assert calls == [
        ["codex", "mcp", "add", "parallelizer", "--",
         "uv", "run", "--project", "/repo", "python", "/repo/mcp_server.py"]
    ]


def test_mcp_add_builds_claude_command_with_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(cli, "_parallelizer_repo_root", lambda cwd: Path("/repo"))
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0),
    )

    result = runner.invoke(cli.app, ["mcp", "add", "claude", "--scope", "local"])

    assert result.exit_code == 0
    assert calls == [
        ["claude", "mcp", "add", "--scope", "local", "--transport", "stdio", "parallelizer", "--",
         "uv", "run", "--project", "/repo", "python", "/repo/mcp_server.py"]
    ]


def test_mcp_add_rejects_unknown_agent() -> None:
    result = runner.invoke(cli.app, ["mcp", "add", "vim"])

    assert result.exit_code == 1
    assert "agent must be 'claude' or 'codex'" in result.stderr


def test_mcp_add_errors_when_agent_cli_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_parallelizer_repo_root", lambda cwd: Path("/repo"))
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    result = runner.invoke(cli.app, ["mcp", "add", "codex"])

    assert result.exit_code == 1
    assert "was not found on PATH" in result.stderr


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


def test_codex_background_command_injects_permission_hooks_before_agent_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    service = ParallelizerService(repo)

    command = service.agent_command(
        "codex",
        "background",
        repo,
        "do it",
        agent_args=["--dangerously-bypass-approvals"],
        hook_record_name="alpha",
        hook_state_file=tmp_path / "state.json",
    )

    assert command[:4] == ["codex", "exec", "--cd", str(repo)]
    assert "hooks.PermissionRequest" in command[5]
    assert "parallelizer.agent_event alpha permission-request" in command[5]
    assert "hooks.PreToolUse" in command[7]
    assert "hooks.PostToolUse" in command[9]
    assert "hooks.Stop" in command[11]
    assert command[-2:] == ["--dangerously-bypass-approvals", "do it"]


def test_claude_background_command_injects_permission_hook_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    service = ParallelizerService(repo)

    command = service.agent_command(
        "claude",
        "background",
        repo,
        "do it",
        agent_args=["--verbose"],
        hook_record_name="alpha",
        hook_state_file=tmp_path / "state.json",
    )

    assert command[:2] == ["claude", "-p"]
    settings = json.loads(command[3])
    assert command[2] == "--settings"
    assert "PermissionRequest" in settings["hooks"]
    assert "PreToolUse" in settings["hooks"]
    assert "PostToolUse" in settings["hooks"]
    assert "Stop" in settings["hooks"]
    assert "parallelizer.agent_event alpha permission-request" in json.dumps(settings)
    assert command[-2:] == ["--verbose", "do it"]


def test_fake_background_command_does_not_inject_permission_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _write_config(repo, tmp_path / "worktrees")
    service = ParallelizerService(repo)

    command = service.agent_command(
        "fake",
        "background",
        repo,
        "do it",
        agent_args=["--flag"],
        hook_record_name="alpha",
        hook_state_file=tmp_path / "state.json",
    )

    assert command == [sys.executable, "-c", "print('agent ok')", "--flag", "do it"]


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
    assert "plr wt <name> -- git status --short" in manager
    assert "plr wt <name> -- git diff" in manager
    assert "plr merge <name>" in manager
    assert "plr rm <name>" in manager
    assert "sleep 7" in manager
    assert "sandbox escalation" in manager
    assert "ship feature" in manager
    assert "setup_environment" in setup
    assert "cleanup_environment" in setup
    assert "PLR_SOURCE_REPO" in setup
    assert "PLR_WORKTREE" in setup
    assert "use port offsets" in setup


def test_instructions_markdown_documents_commands_and_setup() -> None:
    instructions = plr_instructions_markdown()

    assert "plr sub [name] [prompt]" in instructions
    assert "plr wt NAME -- CMD..." in instructions
    assert "plr wt <name> -- git status --short" in instructions
    assert "plr wt <name> -- git diff" in instructions
    assert "plr agent setup [instructions]" in instructions
    assert "plr agent setup_plr" not in instructions
    assert "plr merge NAME" in instructions
    assert "plr rm [NAME]" in instructions
    assert "plr rm '*'" in instructions
    assert "plr remove '*'" in instructions
    assert "setup_environment" in instructions
    assert "cleanup_environment" in instructions
    assert "allocated worktree number as `$1`" in instructions
    assert "PLR_SOURCE_REPO" in instructions
    assert "PLR_WORKTREE" in instructions
    assert "sleep 10" in instructions
    assert "sandbox escalation" in instructions


def test_agent_setup_command_accepts_optional_instruction_string(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    service = SimpleNamespace()
    monkeypatch.setattr(cli, "ParallelizerService", lambda path: service)
    monkeypatch.setattr(
        cli,
        "_exec_current_repo_agent",
        lambda *args: calls.append(args),
    )

    result = runner.invoke(cli.app, ["agent", "setup", "use port offsets"])

    assert result.exit_code == 0
    assert len(calls) == 1
    called_service, prompt, agent, model, agent_args = calls[0]
    assert called_service is service
    assert "use port offsets" in prompt
    assert "setup_environment" in prompt
    assert agent is None
    assert model is None
    assert agent_args == []


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


def test_cd_consumes_fzf_selection_and_starts_shell_in_worktree(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [
        SimpleNamespace(name="alpha", status="done", worktree_path="/tmp/alpha"),
        SimpleNamespace(name="beta", status="done", worktree_path="/tmp/beta"),
    ]
    cd_calls = []
    exec_calls = []
    fzf_inputs = []
    service = SimpleNamespace(
        project=SimpleNamespace(root=Path("/tmp/repo")),
        list_records=lambda: records,
        worktree_info=lambda name: {"worktree_path": f"/tmp/{name}"},
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/bin/fzf" if name == "fzf" else None)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: (
            fzf_inputs.append(kwargs["input"])
            or SimpleNamespace(returncode=0, stdout="beta\tdone\t/tmp/beta\n")
        ),
    )
    monkeypatch.setattr(cli.os, "chdir", lambda path: cd_calls.append(path))
    monkeypatch.setattr(cli.os, "execvp", lambda file, args: exec_calls.append((file, args)))

    cli._cd(service, None)

    assert fzf_inputs[0].splitlines()[0] == "main\tsource\t/tmp/repo"
    assert cd_calls == ["/tmp/beta"]
    assert exec_calls == [("/bin/zsh", ["zsh"])]


def test_cd_can_select_main_branch_from_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    cd_calls = []
    exec_calls = []
    service = SimpleNamespace(
        project=SimpleNamespace(root=Path("/tmp/repo")),
        list_records=lambda: [SimpleNamespace(name="alpha", status="done", worktree_path="/tmp/alpha")],
        worktree_info=lambda name: {"worktree_path": f"/tmp/{name}"},
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/bin/fzf" if name == "fzf" else None)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="main\tsource\t/tmp/repo\n"),
    )
    monkeypatch.setattr(cli.os, "chdir", lambda path: cd_calls.append(path))
    monkeypatch.setattr(cli.os, "execvp", lambda file, args: exec_calls.append((file, args)))

    cli._cd(service, None)

    assert cd_calls == ["/tmp/repo"]
    assert exec_calls == [("/bin/zsh", ["zsh"])]


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


def _init_parallelizer_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], repo)
    (repo / "mcp_server.py").write_text("# entrypoint\n")
    (repo / "pyproject.toml").write_text('[project]\nname = "parallelizer"\n')
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


def _commit(cwd: Path, message: str) -> None:
    _run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", message], cwd)


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True)


def _record(name: str, worktree: Path) -> TreeRecord:
    return TreeRecord(
        name=name,
        source_repo=str(worktree.parent),
        worktree_path=str(worktree),
        branch=f"plr/{name}",
        allocation_number=1,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
