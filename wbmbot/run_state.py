"""Per-repository incident and uncertain-submission state, persisted on the log branch."""
import json
from pathlib import Path


class RunState:
    def __init__(self, path="logs/state.json"):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}
        if not isinstance(self.data, dict):
            raise ValueError("Invalid bot state; restore logs/state.json from the log branch")
        self.data.setdefault("query_failures", 0)
        self.data.setdefault("incidents", {})
        self.data.setdefault("pending", {})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2) + "\n")
        temporary.replace(self.path)

    def query_failed(self):
        self.data["query_failures"] += 1
        self.save()
        return self.data["query_failures"]

    def query_succeeded(self):
        self.data["query_failures"] = 0
        self.data["incidents"].pop("query", None)
        self.save()

    def mark_pending(self, flat):
        self.data["pending"][flat.hash] = True
        self.save()

    def clear_pending(self, flat):
        self.data["pending"].pop(flat.hash, None)
        self.data["incidents"].pop(flat.hash, None)
        self.save()
