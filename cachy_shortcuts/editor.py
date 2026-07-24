"""Safe mutation of compositor configs.

Every operation follows the same shape:

    snapshot -> compute new text -> atomic write -> re-parse to validate
             -> reload, or roll back if validation failed

Edits are surgical: the binding's recorded span is replaced and nothing else in
the file moves, so comments, ordering and hand-tuned formatting survive.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import backup
from .backends.base import Backend
from .backends.cosmic import CosmicBackend
from .model import Chord, Shortcut


class EditError(RuntimeError):
    """Raised when a write could not be applied, after rollback."""


@dataclass
class EditResult:
    operation: str
    path: Path
    snapshot: backup.Snapshot
    chord: Chord | None = None

    def describe(self) -> str:
        target = self.chord.display() if self.chord else ""
        return f"{self.operation} {target} in {self.path}".strip()


def _target_file(backend: Backend, shortcut: Shortcut | None) -> Path:
    """Which file to write to.

    COSMIC is the interesting case: its ``defaults`` file is system-owned and
    read-only, so an edit to a default binding becomes an override written into
    the user's ``custom`` file instead.
    """
    if isinstance(backend, CosmicBackend):
        return backend.write_target()
    if shortcut is not None and shortcut.source is not None:
        return shortcut.source.path
    paths = backend.config_paths()
    if not paths:
        raise EditError(f"no writable config found for {backend.display_name}")
    return paths[0]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise EditError(f"cannot read {path}: {exc}") from exc


def _commit(
    backend: Backend,
    path: Path,
    new_text: str,
    operation: str,
    validate,
    chord: Chord | None,
) -> EditResult:
    snapshot = backup.create([path], reason=operation)
    backup.write_atomic(path, new_text)
    try:
        reparsed = backend.parse(path.read_text(encoding="utf-8"), path)
    except Exception as exc:  # noqa: BLE001 - any parse failure means roll back
        backup.restore(snapshot)
        raise EditError(f"{operation} produced an unparsable file, rolled back: {exc}")
    if not validate(reparsed):
        backup.restore(snapshot)
        raise EditError(f"{operation} did not take effect, rolled back")
    backend.reload()
    backup.prune()
    return EditResult(operation=operation, path=path, snapshot=snapshot, chord=chord)


def add(
    backend: Backend,
    chord: Chord,
    action: str,
    description: str = "",
) -> EditResult:
    path = _target_file(backend, None)
    text = _read(path)
    offset, prefix, suffix = backend.insertion_point(text)
    rendered = backend.render(chord, action, description)
    new_text = text[:offset] + prefix + rendered + suffix + text[offset:]
    return _commit(
        backend,
        path,
        new_text,
        "add",
        lambda parsed: any(s.chord == chord for s in parsed),
        chord,
    )


def rebind(backend: Backend, shortcut: Shortcut, new_chord: Chord) -> EditResult:
    """Change which keys trigger an existing binding."""
    return _replace(
        backend,
        shortcut,
        new_chord=new_chord,
        new_action=shortcut.action,
        new_description=shortcut.description,
        operation="rebind",
    )


def retarget(backend: Backend, shortcut: Shortcut, new_action: str) -> EditResult:
    """Change what an existing binding does."""
    return _replace(
        backend,
        shortcut,
        new_chord=shortcut.chord,
        new_action=new_action,
        new_description=shortcut.description,
        operation="retarget",
    )


def relabel(backend: Backend, shortcut: Shortcut, new_description: str) -> EditResult:
    return _replace(
        backend,
        shortcut,
        new_chord=shortcut.chord,
        new_action=shortcut.action,
        new_description=new_description,
        operation="relabel",
    )


def _replace(
    backend: Backend,
    shortcut: Shortcut,
    new_chord: Chord,
    new_action: str,
    new_description: str,
    operation: str,
) -> EditResult:
    if shortcut.source is None:
        raise EditError("binding has no recorded source span")

    rendered = backend.render(new_chord, new_action, new_description, shortcut.extras)

    # A COSMIC default lives in a system file we must not touch; write an
    # override into the user's custom file instead of editing in place.
    if shortcut.extras.get("readonly"):
        path = _target_file(backend, None)
        text = _read(path)
        offset, prefix, suffix = backend.insertion_point(text)
        new_text = text[:offset] + prefix + rendered + suffix + text[offset:]
        return _commit(
            backend,
            path,
            new_text,
            f"{operation} (override)",
            lambda parsed: any(s.chord == new_chord for s in parsed),
            new_chord,
        )

    path = shortcut.source.path
    text = _read(path)
    if text[shortcut.source.start : shortcut.source.end] != shortcut.raw:
        raise EditError(
            f"{path} changed since it was read; refusing to edit the wrong bytes"
        )
    new_text = text[: shortcut.source.start] + rendered + text[shortcut.source.end :]
    return _commit(
        backend,
        path,
        new_text,
        operation,
        lambda parsed: any(s.chord == new_chord for s in parsed),
        new_chord,
    )


def delete(backend: Backend, shortcut: Shortcut) -> EditResult:
    if shortcut.source is None:
        raise EditError("binding has no recorded source span")

    # Removing a COSMIC default means recording an explicit Disable in custom.
    if shortcut.extras.get("readonly"):
        path = _target_file(backend, None)
        text = _read(path)
        offset, prefix, suffix = backend.insertion_point(text)
        rendered = backend.render(shortcut.chord, "Disable")
        new_text = text[:offset] + prefix + rendered + suffix + text[offset:]
        return _commit(
            backend,
            path,
            new_text,
            "disable",
            lambda parsed: any(s.chord == shortcut.chord for s in parsed),
            shortcut.chord,
        )

    path = shortcut.source.path
    text = _read(path)
    if text[shortcut.source.start : shortcut.source.end] != shortcut.raw:
        raise EditError(
            f"{path} changed since it was read; refusing to edit the wrong bytes"
        )
    start, end = shortcut.source.start, shortcut.source.end
    # Take the whole line when the binding was alone on it, so deleting does
    # not leave a blank gap behind.
    line_start = text.rfind("\n", 0, start) + 1
    if not text[line_start:start].strip():
        start = line_start
        newline = text.find("\n", end)
        end = len(text) if newline == -1 else newline + 1
    new_text = text[:start] + text[end:]
    return _commit(
        backend,
        path,
        new_text,
        "delete",
        lambda parsed: all(s.chord != shortcut.chord for s in parsed),
        shortcut.chord,
    )


def undo_last() -> list[Path]:
    """Roll back the most recent write."""
    return backup.restore_latest()
