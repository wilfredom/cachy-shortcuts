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
from collections import Counter
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
    reason = backend.unsupported()
    if reason:
        raise EditError(reason)
    if isinstance(backend, CosmicBackend):
        return backend.write_target()
    if shortcut is not None and shortcut.source is not None:
        return backend.write_path(shortcut.source.path)
    paths = backend.config_paths()
    if not paths:
        raise EditError(f"no writable config found for {backend.display_name}")
    return backend.write_path(paths[0])


def read_for_edit(backend: Backend, path: Path) -> str:
    """The text an edit to ``path`` starts from.

    A file the edit is about to create starts as whatever the backend seeds
    it with (mango: a copy of the system config it replaces).
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    except (OSError, UnicodeDecodeError) as exc:
        raise EditError(f"cannot read {path}: {exc}") from exc
    try:
        return backend.seed_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise EditError(
            f"cannot create {path}: the config it replaces can't be read "
            f"to copy from ({exc})"
        ) from exc


def _write(snapshot: backup.Snapshot, path: Path, text: str) -> None:
    """Atomic write; a refusal (read-only dir, no permission) is an EditError."""
    try:
        backup.write_atomic(path, text)
    except OSError as exc:
        backup.discard(snapshot)  # nothing was written
        raise EditError(f"cannot write {path}: {exc}") from exc


def _insert(backend: Backend, text: str, rendered: str) -> str:
    """``text`` with a new binding placed where the backend wants it."""
    offset, prefix, suffix = backend.insertion_point(text)
    return text[:offset] + prefix + rendered + suffix + text[offset:]


def _commit(
    backend: Backend,
    path: Path,
    new_text: str,
    operation: str,
    validate,
    chord: Chord | None,
) -> EditResult:
    snapshot = backup.create([path], reason=operation)
    _write(snapshot, path, new_text)
    try:
        reparsed = backend.parse(path.read_text(encoding="utf-8"), path)
    except Exception as exc:  # noqa: BLE001 - any parse failure means roll back
        _rollback(snapshot)
        raise EditError(f"{operation} produced an unparsable file, rolled back: {exc}")
    if not validate(reparsed):
        _rollback(snapshot)
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
    snapshot = backup.create([path], reason=operation)
    _write(snapshot, path, new_text)
    try:
        written = path.read_text(encoding="utf-8")
    except OSError as exc:
        _rollback(snapshot)
        raise EditError(f"cannot re-read {path} after {operation}: {exc}") from exc
    if not validate(written):
        _rollback(snapshot)
        raise EditError(f"{operation} did not take effect, rolled back")
    backup.prune()
    return EditResult(operation=operation, path=path, snapshot=snapshot)


def _rollback(snapshot: backup.Snapshot) -> None:
    """Undo a write, including one that created the file.

    The snapshot records a file that did not exist as absent, and restoring
    it deletes the file again. The snapshot is then dropped: nothing changed,
    so it must not become what `undo` restores next.
    """
    backup.restore(snapshot)
    backup.discard(snapshot)


def add(
    backend: Backend,
    chord: Chord,
    action: str,
    description: str = "",
) -> EditResult:
    path = _target_file(backend, None)
    text = read_for_edit(backend, path)
    new_text = _insert(backend, text, backend.render(chord, action, description))
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

    One snapshot covers both writes, so a single `undo` puts the victim back
    too. If the second write fails, that snapshot is the newest one left.
    """
    touched = [_target_file(backend, victim)]
    second = _target_file(backend, target)
    if second not in touched:
        touched.append(second)
    combined = backup.create(touched, reason="take over")
    try:
        removed = delete(backend, victim)
    except Exception:
        backup.discard(combined)  # nothing changed; undo keeps its last edit
        raise
    backup.discard(removed.snapshot)
    if target is None:
        result = add(backend, chord, action, description)
    else:
        fresh = _relocate(backend, target)
        result = update(
            backend, fresh, chord=chord, action=action, description=description
        )
    backup.discard(result.snapshot)
    result.snapshot = combined
    return result


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
        _require_live_default(backend, shortcut)
        text = read_for_edit(backend, path)
        old_chord = shortcut.chord
        released = new_chord != old_chord
        new_text = text
        if released:
            # COSMIC merges custom over defaults chord by chord, so an override
            # at a new chord leaves the default live at the old one as well.
            # Disable it in the same write, as COSMIC Settings does.
            new_text = _insert(backend, new_text, backend.render(old_chord, "Disable"))
        new_text = _insert(backend, new_text, rendered)

        def took(parsed: list[Shortcut]) -> bool:
            if not any(s.chord == new_chord for s in parsed):
                return False
            return not released or any(
                s.chord == old_chord and s.action.startswith("Disable")
                for s in parsed
            )

        return _commit(
            backend, path, new_text, f"{operation} (override)", took, new_chord
        )

    path = _target_file(backend, shortcut)
    text = read_for_edit(backend, path)
    if text[shortcut.source.start : shortcut.source.end] != shortcut.raw:
        raise EditError(
            f"{path} changed since it was read; refusing to edit the wrong bytes"
        )
    new_text = text[: shortcut.source.start] + rendered + text[shortcut.source.end :]
    old_chord = shortcut.chord
    shield = (
        isinstance(backend, CosmicBackend)
        and new_chord != old_chord
        and backend.default_at(old_chord) is not None
        and not any(s.chord == old_chord for s in backend.parse(new_text, path))
    )
    if shield:
        # This entry was overriding a default on its old chord. Moved off it,
        # the default would come back there unasked; keep it off, as moving
        # a default does.
        new_text = _insert(backend, new_text, backend.render(old_chord, "Disable"))

    def took(parsed: list[Shortcut]) -> bool:
        if not any(s.chord == new_chord for s in parsed):
            return False
        return not shield or any(
            s.chord == old_chord and s.action.startswith("Disable") for s in parsed
        )

    return _commit(backend, path, new_text, operation, took, new_chord)


def _require_live_default(backend: Backend, shortcut: Shortcut) -> None:
    """Refuse to override a read-only default that is no longer what's live.

    An override is appended to the user's file, and the last entry for a
    chord wins -- so a Disable or override written for a stale default would
    silently beat whatever took the chord since it was read (COSMIC Settings
    while the overlay is open, a second CLI run). The in-place path has its
    span check for this; this is the same check for the append path.
    """
    live = next((s for s in backend.read() if s.chord == shortcut.chord), None)
    if live is None or not live.extras.get("readonly") or live.raw != shortcut.raw:
        raise EditError(
            f"{shortcut.chord.display()} changed since it was read; refusing "
            "to override it -- reopen the list and try again"
        )


def delete(backend: Backend, shortcut: Shortcut) -> EditResult:
    if shortcut.source is None:
        raise EditError("binding has no recorded source span")

    # Removing a COSMIC default means recording an explicit Disable in custom.
    if shortcut.extras.get("readonly"):
        path = _target_file(backend, None)
        _require_live_default(backend, shortcut)
        text = read_for_edit(backend, path)
        new_text = _insert(backend, text, backend.render(shortcut.chord, "Disable"))
        return _commit(
            backend,
            path,
            new_text,
            "disable",
            lambda parsed: any(s.chord == shortcut.chord for s in parsed),
            shortcut.chord,
        )

    path = _target_file(backend, shortcut)
    text = read_for_edit(backend, path)
    if text[shortcut.source.start : shortcut.source.end] != shortcut.raw:
        raise EditError(
            f"{path} changed since it was read; refusing to edit the wrong bytes"
        )
    # Every other binding in the file must come through untouched: the same
    # chord can legitimately be bound again here (a duplicate, a Hyprland
    # submap, a mango keymode), and a sibling on the same line must not go
    # with the victim.
    expected = _bindings(backend.parse(text, path))
    expected[(shortcut.chord.canonical, shortcut.action, shortcut.raw)] -= 1
    start, end = backend.deletion_span(
        text, shortcut.source.start, shortcut.source.end
    )
    new_text = text[:start] + text[end:]
    return _commit(
        backend,
        path,
        new_text,
        "delete",
        lambda parsed: _bindings(parsed) == +expected,
        shortcut.chord,
    )


def _bindings(parsed: list[Shortcut]) -> Counter:
    """A file's bindings as a multiset, for comparing before and after."""
    return Counter((s.chord.canonical, s.action, s.raw) for s in parsed)


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
