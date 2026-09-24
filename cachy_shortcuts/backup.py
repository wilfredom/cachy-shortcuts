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
# Dropped into a snapshot once `undo` has restored it, so the next undo walks
# further back instead of restoring the same snapshot again.
UNDONE = "undone"


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
    backup_root().mkdir(parents=True, exist_ok=True)
    # Never share a directory with another snapshot: discarding one would
    # take the other with it. A suffix still sorts after the bare stamp.
    target = backup_root() / stamp
    suffix = 0
    while True:
        try:
            target.mkdir()
            break
        except FileExistsError:
            suffix += 1
            target = backup_root() / f"{stamp}-{suffix}"
    stored: list[Path] = []
    manifest: list[dict] = []
    for index, path in enumerate(paths):
        if not path.exists():
            # Record the absence, so restoring removes a file the edit created
            # rather than leaving it behind. Resolved, because a dangling
            # symlink is written through: the file created is its target.
            try:
                original = path.resolve()
            except (OSError, RuntimeError):
                original = path
            stored.append(path)
            manifest.append({"original": str(original), "absent": True})
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
    """Put a snapshot's files back. Returns the paths restored.

    A file the snapshot recorded as absent is deleted, since the write being
    undone is what created it.
    """
    manifest = snapshot.path / MANIFEST
    data = json.loads(manifest.read_text(encoding="utf-8"))
    restored: list[Path] = []
    for entry in data.get("files", []):
        original = Path(entry["original"])
        if entry.get("absent"):
            if original.exists():
                original.unlink()
                restored.append(original)
            continue
        stored = snapshot.path / entry["stored"]
        if not stored.is_file():
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stored, original)
        restored.append(original)
    return restored


def restore_latest() -> list[Path]:
    """Restore the newest snapshot not already undone, and mark it undone.

    A marker rather than deleting the snapshot keeps `restore --list` history
    intact; skipping marked ones is what makes a second undo walk back.
    """
    for snapshot in list_snapshots():
        if (snapshot.path / UNDONE).exists():
            continue
        restored = restore(snapshot)
        (snapshot.path / UNDONE).touch()
        return restored
    return []


def discard(snapshot: Snapshot) -> None:
    """Drop a snapshot that no longer stands for an edit on disk.

    A write that rolled back changed nothing, and leaving its snapshot as the
    newest would make `undo` restore that no-op instead of the last real edit.
    """
    shutil.rmtree(snapshot.path, ignore_errors=True)


def write_atomic(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename.

    Same-directory temp keeps the rename on one filesystem, so it is atomic and
    the config can never be observed half-written by a compositor that is
    watching the file.

    A symlinked config is written through, as ``restore``'s copy does: the
    rename lands on the link's target, so the link survives and the dotfile it
    points at gets the edit.
    """
    path = path.resolve()
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
