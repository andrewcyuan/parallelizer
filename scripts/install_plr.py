#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the plr command with uv.")
    parser.add_argument(
        "--update-shell",
        action="store_true",
        help="Run `uv tool update-shell` if uv's tool bin directory is not on PATH.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the commands that would run without installing anything.",
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    uv = shutil.which("uv")
    if not uv:
        print("Error: uv is required to install plr.", file=sys.stderr)
        return 1
    if sys.version_info < (3, 10):
        print("Error: plr requires Python 3.10 or newer.", file=sys.stderr)
        return 1

    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    install_cmd = [uv, "tool", "install", "--editable", str(repo), "--python", python_version]
    if not _run(install_cmd, args.dry_run):
        return 1

    bin_dir = _uv_tool_bin_dir(uv, args.dry_run)
    if bin_dir and _path_contains(bin_dir):
        print(f"Installed plr. `{bin_dir}` is already on PATH.")
        return 0

    if args.update_shell:
        if not _run([uv, "tool", "update-shell"], args.dry_run):
            return 1
        print("Shell PATH update requested. Restart your shell before running `plr`.")
        return 0

    if bin_dir:
        print(f"Installed plr into: {bin_dir}")
        print("That directory is not currently on PATH.")
        print("Run this once to update your shell startup file:")
        print("  uv tool update-shell")
    return 0


def _run(command: list[str], dry_run: bool) -> bool:
    print("+ " + " ".join(command))
    if dry_run:
        return True
    result = subprocess.run(command, check=False)
    return result.returncode == 0


def _uv_tool_bin_dir(uv: str, dry_run: bool) -> Path | None:
    command = [uv, "tool", "dir", "--bin"]
    print("+ " + " ".join(command))
    if dry_run:
        return None
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        print(result.stderr.strip() or "Warning: could not determine uv tool bin directory.")
        return None
    return Path(result.stdout.strip()).expanduser()


def _path_contains(directory: Path) -> bool:
    resolved = directory.resolve()
    for item in os.environ.get("PATH", "").split(os.pathsep):
        if item and Path(item).expanduser().resolve() == resolved:
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
