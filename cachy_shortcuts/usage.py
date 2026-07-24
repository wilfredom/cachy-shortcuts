"""Learning mode: track what you look up, not what you fire.

This tool deliberately doesn't execute your shortcuts, so it can't count how
often you press them. It counts something more useful for learning: how often
you had to come *here* to find a binding. A shortcut you keep searching for is
one you haven't internalised yet, which is exactly what a learning aid should
surface.

Everything stays on disk locally; nothing is transmitted anywhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .backup import data_dir


def store_path() -> Path:
    return data_dir() / "usage.json"


@dataclass
class Gap:
    chord: str
    count: int
    last_seen: str

    def describe(self) -> str:
        return f"{self.chord} (looked up {self.count}x)"


def _load() -> dict:
    path = store_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"lookups": {}}


def _save(data: dict) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def record_lookup(chord_canonical: str) -> None:
    """Note that the user had to come here to find this binding."""
    data = _load()
    lookups = data.setdefault("lookups", {})
    entry = lookups.setdefault(chord_canonical, {"count": 0, "last": ""})
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save(data)


def top_gaps(limit: int = 5, minimum: int = 2) -> list[Gap]:
    """The bindings looked up most often.

    ``minimum`` keeps one-off lookups out of the list -- looking something up
    once is not a gap, it's just usage.
    """
    data = _load()
    gaps = [
        Gap(chord=chord, count=int(v.get("count", 0)), last_seen=v.get("last", ""))
        for chord, v in data.get("lookups", {}).items()
        if int(v.get("count", 0)) >= minimum
    ]
    gaps.sort(key=lambda g: (-g.count, g.chord))
    return gaps[:limit]


def counts() -> dict[str, int]:
    data = _load()
    return {k: int(v.get("count", 0)) for k, v in data.get("lookups", {}).items()}


def forget_all() -> bool:
    """Erase the usage history. Returns whether anything was there."""
    path = store_path()
    if not path.exists():
        return False
    path.unlink()
    return True


def forget(chord_canonical: str) -> bool:
    data = _load()
    if chord_canonical in data.get("lookups", {}):
        del data["lookups"][chord_canonical]
        _save(data)
        return True
    return False
