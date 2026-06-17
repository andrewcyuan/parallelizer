from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .service import utc_now
from .state import StateStore

MAX_SUMMARY_LENGTH = 240
MAX_PAYLOAD_BYTES = 4096


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("event", choices=["permission-request", "clear-permission"])
    parser.add_argument("--state-file", required=True)
    args = parser.parse_args()

    try:
        payload = _read_payload(sys.stdin.read())
        apply_event(Path(args.state_file), args.name, args.event, payload)
    except Exception:
        return


def apply_event(state_file: Path, name: str, event: str, payload: Optional[Dict[str, Any]]) -> None:
    store = StateStore(state_file)
    data = store.load()
    record = data.get("trees", {}).get(name)
    if not record:
        return

    if event == "permission-request":
        record["pending_permission"] = _pending_permission(record.get("agent"), payload or {})
    elif event == "clear-permission":
        record["pending_permission"] = None
    else:
        return

    record["updated_at"] = utc_now()
    store.save(data)


def _read_payload(raw: str) -> Optional[Dict[str, Any]]:
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _pending_permission(agent: Optional[str], payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "agent": agent,
        "tool": _truncate(_tool_name(payload), 80),
        "summary": _truncate(_summary(payload), MAX_SUMMARY_LENGTH),
        "payload": _bounded_payload(payload),
        "requested_at": utc_now(),
    }


def _tool_name(payload: Dict[str, Any]) -> str:
    for key in ("tool", "tool_name", "name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        value = tool_input.get("name")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _summary(payload: Dict[str, Any]) -> str:
    for key in ("summary", "message", "reason", "permission"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    tool = _tool_name(payload)
    return f"Permission requested for {tool}" if tool != "unknown" else "Permission requested"


def _bounded_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    encoded = json.dumps(payload, sort_keys=True, default=str)
    if len(encoded.encode("utf-8")) <= MAX_PAYLOAD_BYTES:
        return payload
    return {
        "truncated": True,
        "preview": encoded.encode("utf-8")[:MAX_PAYLOAD_BYTES].decode("utf-8", errors="ignore"),
    }


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


if __name__ == "__main__":
    main()
