from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import TreeRecord


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"next_number": 1, "trees": {}}
        with self.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        data.setdefault("next_number", 1)
        data.setdefault("trees", {})
        return data

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp.replace(self.path)

    def records(self) -> List[TreeRecord]:
        data = self.load()
        return [TreeRecord.from_dict(item) for item in data["trees"].values()]

    def get(self, name: str) -> Optional[TreeRecord]:
        item = self.load()["trees"].get(name)
        return TreeRecord.from_dict(item) if item else None

    def put(self, record: TreeRecord) -> None:
        data = self.load()
        data["trees"][record.name] = record.to_dict()
        self.save(data)

    def allocate_number(self) -> int:
        data = self.load()
        number = int(data.get("next_number", 1))
        data["next_number"] = number + 1
        self.save(data)
        return number
