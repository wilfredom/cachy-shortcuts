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
from dataclasses import dataclass, field
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
    # take_over: other bindings on the chord it removed besides the victim.
    also_removed: list[Shortcut] = field(default_factory=list)

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
    live: tuple[str, bool] | None = None,
) -> EditResult:
    """Snapshot, write, validate, and roll back if the edit didn't take.

    ``validate`` checks the file just written. ``live`` -- the rendered
    binding, and whether it must be global -- also checks the config as the
    compositor loads it: a binding can be in its file and still never fire,
    removed by a later Hyprland ``unbind`` or beaten by an earlier mango bind
    on the same keys.
    """
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
    if live is not None and chord is not None:
        why = _why_not_live(backend, path, chord, *live)
        if why:
            _rollback(snapshot)
            raise EditError(
                f"{operation} rolled back: {chord.display()} would not fire, "
                f"because {why}"
            )
    backend.reload()
    backup.prune()
    return EditResult(operation=operation, path=path, snapshot=snapshot, chord=chord)


def _why_dead(shortcut: Shortcut) -> str | None:
    """Why a binding the compositor loads never fires, or None if it does."""
    if shortcut.extras.get("disabled"):
        return f"{shortcut.extras.get('disabled_by') or 'a later unbind'} removes it"
    if shortcut.extras.get("shadowed_by"):
        return (
            "an earlier bind on the same keys runs instead: "
            f"{shortcut.extras['shadowed_by']}"
        )
    return None


def _why_not_live(
    backend: Backend, path: Path, chord: Chord, rendered: str, global_only: bool
) -> str | None:
    """Why the binding just written to ``path`` would not fire, if it wouldn't.

    Found in ``read()`` by its text; failing that, by its chord in that file.
    A binding ``read()`` doesn't list at all is left to the file check.
    """
    shortcuts = backend.read()
    mine = [s for s in shortcuts if s.chord == chord and s.raw == rendered.strip()]
    if not mine:
        mine = [
            s
            for s in shortcuts
            if s.chord == chord
            and s.source is not None
            and backend.write_path(s.source.path) == path
        ]
    reasons: list[str] = []
    for shortcut in mine:
        why = _why_dead(shortcut)
        if why is None and global_only and shortcut.extras.get("submap"):
            why = f"it lands inside the {shortcut.extras['submap']!r} mode"
        if why is None:
            return None
        reasons.append(why)
    return reasons[0] if reasons else None


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
    rendered = backend.render(chord, action, description)
    offset, prefix, suffix = backend.insertion_point(text)
    moved = backend.placement(path, offset, rendered)
    if moved is not None:
        path, offset, prefix, suffix = moved
        text = read_for_edit(backend, path)
    new_text = text[:offset] + prefix + rendered + suffix + text[offset:]
    return _commit(
        backend,
        path,
        new_text,
        "add",
        lambda parsed: any(s.chord == chord for s in parsed),
        chord,
        live=(rendered, True),
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
    too. It is built from the snapshots of the writes themselves, so it holds
    every file that was written, whichever that turned out to be. If the
    second write fails, that snapshot is the newest one left.
    """
    combined = backup.create([_target_file(backend, victim)], reason="take over")
    try:
        removed = delete(backend, victim)
    except Exception:
        backup.discard(combined)  # nothing changed; undo keeps its last edit
        raise
    backup.absorb(combined, removed.snapshot)
    # Every other binding on the chord in the victim's scope goes too. In
    # Hyprland they would all fire alongside the new one; in mango the first
    # one left would fire instead of it (keyboard.c stops at the first match).
    also_removed: list[Shortcut] = []
    tried: set[tuple[Path, str]] = set()
    while True:
        other = _rival(backend, victim, target, tried)
        if other is None:
            break
        tried.add((backend.write_path(other.source.path), other.raw))
        extra = delete(backend, other)
        backup.absorb(combined, extra.snapshot)
        also_removed.append(other)
    if target is None:
        result = add(backend, chord, action, description)
    else:
        fresh = _relocate(backend, target)
        result = update(
            backend, fresh, chord=chord, action=action, description=description
        )
    backup.absorb(combined, result.snapshot)
    result.snapshot = combined
    result.also_removed = also_removed
    return result


def _rival(
    backend: Backend,
    victim: Shortcut,
    target: Shortcut | None,
    tried: set[tuple[Path, str]],
) -> Shortcut | None:
    """Another binding still on the victim's chord, in the victim's scope.

    Unbound ones are already gone, and read-only defaults (COSMIC) lose to
    the user's file whatever it binds, so neither needs removing.
    """
    scope = victim.extras.get("submap") or ""
    avoid = set(tried)
    if target is not None and target.source is not None:
        avoid.add((backend.write_path(target.source.path), target.raw))
    for shortcut in backend.read():
        if (
            shortcut.chord == victim.chord
            and (shortcut.extras.get("submap") or "") == scope
            and shortcut.source is not None
            and not shortcut.extras.get("disabled")
            and not shortcut.extras.get("readonly")
            and (backend.write_path(shortcut.source.path), shortcut.raw) not in avoid
        ):
            return shortcut
    return None


def _relocate(backend: Backend, shortcut: Shortcut) -> Shortcut:
    """Find ``shortcut`` again in a freshly parsed config.

    The same text in the same file, not merely the same chord and action:
    Hyprland's override idiom leaves an unbound copy of a bind in an earlier
    file, and editing that one would leave the live bind where it was.
    Among identical lines, one as live as the original wins.
    """
    home = backend.write_path(shortcut.source.path) if shortcut.source else None
    matches = [
        candidate
        for candidate in backend.read()
        if candidate.source is not None
        and candidate.raw == shortcut.raw
        and candidate.chord == shortcut.chord
        and candidate.action == shortcut.action
        and backend.write_path(candidate.source.path) == home
    ]
    disabled = bool(shortcut.extras.get("disabled"))
    matches.sort(key=lambda c: bool(c.extras.get("disabled")) != disabled)
    if matches:
        return matches[0]
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
            backend,
            path,
            new_text,
            f"{operation} (override)",
            took,
            new_chord,
            live=(rendered, False),
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

    # A binding that fired before must still fire after; one that was already
    # dead (unbound, shadowed) can be edited as it is.
    live = (rendered, False) if _why_dead(shortcut) is None else None
    return _commit(backend, path, new_text, operation, took, new_chord, live=live)


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
