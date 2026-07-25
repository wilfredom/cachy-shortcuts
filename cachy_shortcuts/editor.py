"""Safe mutation of compositor configs.

Every operation follows the same shape:

    snapshot -> compute new text -> atomic write -> re-parse to validate
             -> reload, or roll back if validation failed

Edits are surgical: the binding's recorded span is replaced and nothing else in
the file moves, so comments, ordering and hand-tuned formatting survive.
"""

from __future__ import annotations

import re
import shlex
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


def write_file(
    path: Path,
    new_text: str,
    operation: str,
    validate,
) -> EditResult:
    """The same snapshot/atomic/validate/rollback path, for non-binding files.

    ``_commit`` validates by re-parsing with a backend, which only makes sense
    for a file full of bindings. The tiling-exception rules live in files that
    aren't (COSMIC keeps them in a different config context entirely), so the
    caller supplies the check instead.
    """
    existed = path.exists()
    snapshot = backup.create([path], reason=operation)
    backup.write_atomic(path, new_text)
    try:
        written = path.read_text(encoding="utf-8")
    except OSError as exc:
        _rollback(snapshot, path, existed)
        raise EditError(f"cannot re-read {path} after {operation}: {exc}") from exc
    if not validate(written):
        _rollback(snapshot, path, existed)
        raise EditError(f"{operation} did not take effect, rolled back")
    backup.prune()
    return EditResult(operation=operation, path=path, snapshot=snapshot)


def _rollback(snapshot: backup.Snapshot, path: Path, existed: bool) -> None:
    """Undo a write, including one that created the file.

    ``backup.create`` skips paths that don't exist yet, so restoring a snapshot
    of a brand-new file puts nothing back and leaves the bad file in place.
    Deleting it is the honest rollback in that case.
    """
    backup.restore(snapshot)
    if not existed:
        try:
            path.unlink()
        except OSError:
            pass


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


def update(
    backend: Backend,
    shortcut: Shortcut,
    chord: Chord | None = None,
    action: str | None = None,
    description: str | None = None,
) -> EditResult:
    """Change any combination of chord, action and description in one write.

    The editing form can change all three at once, and doing that as three
    calls would be wrong rather than merely wasteful: the first write moves
    every span after it in the file, so the second would be editing bytes that
    are no longer the binding it was handed.
    """
    return _replace(
        backend,
        shortcut,
        new_chord=shortcut.chord if chord is None else chord,
        new_action=shortcut.action if action is None else action,
        new_description=(
            shortcut.description if description is None else description
        ),
        operation="update",
    )


def take_over(
    backend: Backend,
    victim: Shortcut,
    target: Shortcut | None,
    chord: Chord,
    action: str,
    description: str = "",
) -> EditResult:
    """Give ``chord`` to ``target`` (or to a new binding), unbinding ``victim``.

    Deliberately two writes rather than one. The two bindings can live in
    different files, and even in the same file removing the victim shifts
    every span after it -- so the second write has to work from a freshly
    parsed target, not the stale record the caller is holding.
    """
    delete(backend, victim)
    if target is None:
        return add(backend, chord, action, description)
    fresh = _relocate(backend, target)
    return update(backend, fresh, chord=chord, action=action, description=description)


def _relocate(backend: Backend, shortcut: Shortcut) -> Shortcut:
    """Find ``shortcut`` again in a freshly parsed config."""
    for candidate in backend.read():
        if (
            candidate.chord == shortcut.chord
            and candidate.action == shortcut.action
            and candidate.source is not None
        ):
            return candidate
    raise EditError(
        f"unbound the old claimant, but {shortcut.chord.display()} could not be "
        "found again afterwards -- check with `cachy-shortcuts list`, or "
        "`cachy-shortcuts undo` to roll back"
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


def wrap_command_as_action(backend: Backend, command: str) -> str:
    """Wrap a bare shell command in the backend's spawn syntax.

    Centralised so the escaping rule is applied exactly once: niri's
    spawn-sh takes the whole command as a single double-quoted string, so an
    embedded quote must be escaped or the emitted KDL is invalid. COSMIC's
    own quote-escaping lives in its `render()`, since a bare command there is
    wrapped by the backend itself rather than by the caller.
    """
    command = command.strip()
    if backend.name == "niri":
        escaped = command.replace("\\", "\\\\").replace('"', '\\"')
        return f'spawn-sh "{escaped}"'
    if backend.name == "mango":
        return f"spawn {command}"
    if backend.name == "hyprland":
        return f"exec {command}"
    return command


_NIRI_SPAWN = re.compile(r"^spawn(?:-sh|_shell)?\s+(.*)$", re.DOTALL)
_MANGO_SPAWN = re.compile(r"^spawn\s+(.*)$", re.DOTALL)
_HYPR_EXEC = re.compile(r"^exec\s+(.*)$", re.DOTALL)
_COSMIC_SPAWN = re.compile(r'^Spawn\(\s*"(.*)"\s*\)$', re.DOTALL)


def unwrap_action(backend: Backend, action: str) -> str | None:
    """The bare command inside a spawn action, or None if it isn't one.

    The inverse of ``wrap_command_as_action``, and the reason the edit form
    can show you ``firefox`` instead of ``spawn-sh "firefox"``. Returning None
    for a native compositor action (``close-window``, ``Move(Left)``) is what
    stops the form from re-wrapping one into ``spawn-sh "close-window"`` when
    you only meant to change its chord.
    """
    text = action.strip()
    if backend.name == "niri":
        match = _NIRI_SPAWN.match(text)
        if not match:
            return None
        # `spawn "wpctl" "set-volume" "5%+"` is one argument per token, while
        # `spawn-sh "..."` is a single shell string; joining the unquoted
        # tokens with spaces reproduces both as something runnable.
        try:
            parts = shlex.split(match.group(1))
        except ValueError:
            return match.group(1).strip().strip('"')
        return " ".join(parts)
    if backend.name == "mango":
        match = _MANGO_SPAWN.match(text)
        return match.group(1).strip() if match else None
    if backend.name == "hyprland":
        match = _HYPR_EXEC.match(text)
        return match.group(1).strip() if match else None
    match = _COSMIC_SPAWN.match(text)
    if not match:
        return None
    return match.group(1).replace('\\"', '"').replace("\\\\", "\\")
