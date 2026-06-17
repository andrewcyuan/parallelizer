from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List

from .errors import ParallelizerError


def git(args: List[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )


def repo_root(cwd: Path) -> Path:
    result = git(["rev-parse", "--show-toplevel"], cwd)
    if result.returncode != 0:
        raise ParallelizerError("Parallelizer must be run from inside a git repository.")
    return Path(result.stdout.strip()).resolve()


def current_head(repo: Path) -> str:
    result = git(["rev-parse", "HEAD"], repo)
    if result.returncode != 0:
        raise ParallelizerError(result.stderr.strip() or "Unable to resolve HEAD.")
    return result.stdout.strip()


def add_worktree(repo: Path, path: Path, branch: str) -> None:
    result = git(["worktree", "add", "-b", branch, str(path), "HEAD"], repo)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise ParallelizerError(f"Unable to create git worktree: {message}")


def remove_worktree(repo: Path, path: Path) -> None:
    result = git(["worktree", "remove", str(path)], repo)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise ParallelizerError(f"Unable to remove git worktree: {message}")


def merge_branch(repo: Path, branch: str, no_ff: bool = False, squash: bool = False) -> None:
    args = ["merge"]
    if no_ff:
        args.append("--no-ff")
    if squash:
        args.append("--squash")
    args.append(branch)
    result = git(args, repo)
    if result.returncode != 0:
        message = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        raise ParallelizerError(f"Unable to merge {branch}: {message}")


def worktree_porcelain(repo: Path) -> str:
    result = git(["worktree", "list", "--porcelain"], repo)
    if result.returncode != 0:
        raise ParallelizerError(result.stderr.strip() or "Unable to list git worktrees.")
    return result.stdout
