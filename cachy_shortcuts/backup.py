"""Snapshot and rollback for config writes.

This tool edits live compositor config. Every write is preceded by a snapshot
of the files it is about to touch, so a bad edit is always one command from
being undone -- and so a *failed* edit rolls itself back automatically.
"""

from __future__ import annotations

import hashlib
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
    # Creation order. The stamp is local wall-clock time, which can go back
    # (a DST fall-back, a timezone change, a clock correction); undo and
    # prune need the order the edits were actually made in.
    seq: int = 0

    @property
    def id(self) -> str:
        return self.path.name

    def describe(self) -> str:
        names = ", ".join(p.name for p in self.files)
        return f"{self.stamp}  {self.reason}  [{names}]"


def create(paths: list[Path], reason: str = "edit") -> Snapshot:
    """Copy ``paths`` into a timestamped snapshot directory."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    seq = max((s.seq for s in list_snapshots()), default=0) + 1
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
        # Resolved, because write_atomic writes through a symlink: the file
        # an edit changes (or creates) is the link's target, and undo has to
        # go back to that file even if the link is repointed in between.
        try:
            original = path.resolve()
        except (OSError, RuntimeError):
            original = path
        if not path.exists():
            # Record the absence, so restoring removes a file the edit created
            # rather than leaving it behind.
            stored.append(path)
            manifest.append({"original": str(original), "absent": True})
            continue
        # Flatten into the snapshot dir but keep names unique across dirs.
        dest = target / f"{index:02d}_{path.name}"
        shutil.copy2(path, dest)
        stored.append(path)
        manifest.append({"original": str(original), "stored": dest.name})
    (target / MANIFEST).write_text(
        json.dumps(
            {"reason": reason, "stamp": stamp, "seq": seq, "files": manifest},
            indent=2,
        ),
        encoding="utf-8",
    )
    return Snapshot(path=target, stamp=stamp, reason=reason, files=stored, seq=seq)


def list_snapshots() -> list[Snapshot]:
    """Every snapshot, newest first.

    Ordered by the sequence number each was created with; a snapshot from
    before there was one counts as older than any that has one, and those
    keep the order of their stamps.
    """
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
                seq=data.get("seq", 0) if isinstance(data.get("seq"), int) else 0,
            )
        )
    # Stable: equal sequence numbers keep the name order from above.
    out.sort(key=lambda snapshot: snapshot.seq, reverse=True)
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


class UndoRefused(RuntimeError):
    """Undo would destroy changes made to a file after the edit."""

    def __init__(self, snapshot: Snapshot, changed: list[Path]) -> None:
        self.snapshot = snapshot
        self.changed = changed
        names = ", ".join(str(p) for p in changed)
        super().__init__(
            f"{names} changed after that edit ({snapshot.reason}, {snapshot.stamp}), "
            "so undoing it would throw the newer change away. "
            "`cachy-shortcuts undo --force` undoes it anyway, keeping a copy of "
            "the current file(s) in a snapshot of their own."
        )


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def seal(snapshot: Snapshot) -> None:
    """Record what each file holds now that the edit has been written.

    ``restore_latest`` compares against it, so an undo can tell whether a
    file changed after the edit -- COSMIC Settings rewriting ``custom``, a
    hand edit -- and refuse rather than throw that change away.
    """
    manifest = snapshot.path / MANIFEST
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for entry in data.get("files", []):
        entry["after"] = _digest(Path(entry["original"]))
    manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")


def changed_since(snapshot: Snapshot) -> list[Path]:
    """Files that now hold something other than what the edit wrote.

    A file that is gone has nothing to lose. A snapshot never sealed (one
    from an older version, or a write that did not finish) has nothing to
    compare against, so nothing is reported.
    """
    data = json.loads((snapshot.path / MANIFEST).read_text(encoding="utf-8"))
    changed: list[Path] = []
    for entry in data.get("files", []):
        if "after" not in entry:
            continue
        original = Path(entry["original"])
        now = _digest(original)
        if now is not None and now != entry["after"]:
            changed.append(original)
    return changed


def restore_latest(force: bool = False) -> list[Path] | None:
    """Restore the newest snapshot not already undone, and mark it undone.

    Returns the paths restored: empty when that snapshot had nothing left to
    restore (its files are already as they were before the edit), and None
    when there is no snapshot left to undo.

    A file changed after the edit raises UndoRefused. ``force`` undoes it
    anyway, after copying the current files into a snapshot of their own
    (marked undone, so later undos walk past it; ``restore`` brings it back).

    A marker rather than deleting the snapshot keeps `restore --list` history
    intact; skipping marked ones is what makes a second undo walk back.
    """
    for snapshot in list_snapshots():
        if (snapshot.path / UNDONE).exists():
            continue
        changed = changed_since(snapshot)
        if changed:
            if not force:
                raise UndoRefused(snapshot, changed)
            kept = create(changed, reason=f"before forced undo of {snapshot.id}")
            mark_undone(kept)
        restored = restore(snapshot)
        mark_undone(snapshot)
        return restored
    return None


def mark_undone(snapshot: Snapshot) -> None:
    """Leave ``snapshot`` in the history, but out of `undo`'s way."""
    (snapshot.path / UNDONE).touch()


def absorb(into: Snapshot, other: Snapshot) -> None:
    """Fold ``other`` into ``into``, then drop ``other``.

    For an operation made of several writes, so that one snapshot -- and one
    `undo` -- covers every file any of them touched. A file ``into`` already
    holds keeps ``into``'s copy, the older and so the one to go back to.
    """
    manifest = into.path / MANIFEST
    data = json.loads(manifest.read_text(encoding="utf-8"))
    theirs = json.loads((other.path / MANIFEST).read_text(encoding="utf-8"))
    files = data.setdefault("files", [])
    have = {entry["original"] for entry in files}
    for entry in theirs.get("files", []):
        if entry["original"] in have:
            continue
        entry = dict(entry)
        if not entry.get("absent"):
            name = f"{len(files):02d}_{Path(entry['original']).name}"
            shutil.copy2(other.path / entry["stored"], into.path / name)
            entry["stored"] = name
        files.append(entry)
        have.add(entry["original"])
        into.files.append(Path(entry["original"]))
    manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    discard(other)


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
