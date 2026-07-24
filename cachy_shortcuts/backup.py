"""Snapshot and rollback for config writes.

This tool edits live compositor config. Every write is preceded by a snapshot
of the files it is about to touch, so a bad edit is always one command from
being undone -- and so a *failed* edit rolls itself back automatically.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MANIFEST = "manifest.json"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "cachy-shortcuts"


def backup_root() -> Path:
    return data_dir() / "backups"


@dataclass
class Snapshot:
    path: Path
    stamp: str
    reason: str
    files: list[Path]

    @property
    def id(self) -> str:
        return self.path.name

    def describe(self) -> str:
        names = ", ".join(p.name for p in self.files)
        return f"{self.stamp}  {self.reason}  [{names}]"


def create(paths: list[Path], reason: str = "edit") -> Snapshot:
    """Copy ``paths`` into a timestamped snapshot directory."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    target = backup_root() / stamp
    target.mkdir(parents=True, exist_ok=True)
    stored: list[Path] = []
    manifest: list[dict] = []
    for index, path in enumerate(paths):
        if not path.exists():
            continue
        # Flatten into the snapshot dir but keep names unique across dirs.
        dest = target / f"{index:02d}_{path.name}"
        shutil.copy2(path, dest)
        stored.append(path)
        manifest.append({"original": str(path), "stored": dest.name})
    (target / MANIFEST).write_text(
        json.dumps({"reason": reason, "stamp": stamp, "files": manifest}, indent=2),
        encoding="utf-8",
    )
    return Snapshot(path=target, stamp=stamp, reason=reason, files=stored)


def list_snapshots() -> list[Snapshot]:
    root = backup_root()
    if not root.is_dir():
        return []
    out: list[Snapshot] = []
    for entry in sorted(root.iterdir(), reverse=True):
        manifest = entry / MANIFEST
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            Snapshot(
                path=entry,
                stamp=data.get("stamp", entry.name),
                reason=data.get("reason", "?"),
                files=[Path(f["original"]) for f in data.get("files", [])],
            )
        )
    return out


def restore(snapshot: Snapshot) -> list[Path]:
    """Put a snapshot's files back. Returns the paths restored."""
    manifest = snapshot.path / MANIFEST
    data = json.loads(manifest.read_text(encoding="utf-8"))
    restored: list[Path] = []
    for entry in data.get("files", []):
        stored = snapshot.path / entry["stored"]
        original = Path(entry["original"])
        if not stored.is_file():
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stored, original)
        restored.append(original)
    return restored


def restore_latest() -> list[Path]:
    snapshots = list_snapshots()
    if not snapshots:
        return []
    return restore(snapshots[0])


def write_atomic(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename.

    Same-directory temp keeps the rename on one filesystem, so it is atomic and
    the config can never be observed half-written by a compositor that is
    watching the file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            shutil.copystat(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def prune(keep: int = 50) -> int:
    """Drop the oldest snapshots beyond ``keep``. Returns how many were removed."""
    snapshots = list_snapshots()
    removed = 0
    for snapshot in snapshots[keep:]:
        shutil.rmtree(snapshot.path, ignore_errors=True)
        removed += 1
    return removed
