from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Dict

from .state import StateStore
from .service import utc_now


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--command-json", required=True)
    args = parser.parse_args()
    command = json.loads(args.command_json)
    log_path = Path(args.log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        proc = subprocess.Popen(command, cwd=args.worktree, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        exit_code = proc.wait()
    _update_record(Path(args.state_file), args.name, exit_code)


def _update_record(state_file: Path, name: str, exit_code: int) -> None:
    store = StateStore(state_file)
    data: Dict[str, Any] = store.load()
    record = data.get("trees", {}).get(name)
    if not record:
        return
    record["exit_code"] = exit_code
    record["status"] = "done" if exit_code == 0 else "error"
    record["updated_at"] = utc_now()
    store.save(data)


if __name__ == "__main__":
    main()
