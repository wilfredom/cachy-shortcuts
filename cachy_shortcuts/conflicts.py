"""Duplicate-chord detection.

Two bindings claiming the same chord is the usual reason a custom keybind
"mysteriously stops working": the compositor honours one of them and silently
ignores the other. The overlay checks this live while a chord is being
captured, so a collision is visible before it is saved rather than discovered
days later.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby

from .model import Chord, Shortcut


@dataclass
class Conflict:
    chord: Chord
    shortcuts: list[Shortcut]

    @property
    def owners(self) -> list[str]:
        seen: list[str] = []
        for s in self.shortcuts:
            label = s.owner or (s.source.backend if s.source else "unknown")
            if label not in seen:
                seen.append(label)
        return seen

    def describe(self) -> str:
        where = ", ".join(
            f"{s.source.location}" if s.source else "?" for s in self.shortcuts
        )
        return f"{self.chord.display()} claimed {len(self.shortcuts)}x: {where}"


def _scope(shortcut: Shortcut) -> tuple[str, str, str]:
    """What has to match before two bindings are competing for the same chord.

    Beyond the backend, a mode scope: Hyprland's submaps only bind a chord
    while that mode is active, so a submap's Super+H and the global one never
    fire at the same time and are not in conflict.
    """
    return (
        shortcut.source.backend if shortcut.source else "",
        shortcut.extras.get("submap") or "",
        shortcut.chord.canonical,
    )


def find_conflicts(shortcuts: list[Shortcut]) -> list[Conflict]:
    """Chords bound more than once *within the same backend and mode*.

    Cross-backend duplicates are not conflicts: the user only ever runs one
    compositor at a time, and Super+Return meaning "terminal" in all of them is
    the desired outcome, not a problem.
    """
    keyed = sorted(
        (s for s in shortcuts if not s.extras.get("disabled")),
        key=_scope,
    )
    out: list[Conflict] = []
    for _, group in groupby(keyed, key=_scope):
        items = list(group)
        if len(items) > 1:
            out.append(Conflict(chord=items[0].chord, shortcuts=items))
    return out


def claimant(chord: Chord, shortcuts: list[Shortcut]) -> Shortcut | None:
    """The existing *global* binding that already owns ``chord``, if any.

    Mode-scoped bindings (Hyprland submaps) are skipped: they don't stop a
    chord from working outside their mode, so treating one as a claim would
    refuse a perfectly free chord.
    """
    for s in shortcuts:
        if s.extras.get("disabled") or s.extras.get("submap"):
            continue
        if s.chord == chord:
            return s
    return None


def is_available(chord: Chord, shortcuts: list[Shortcut]) -> bool:
    return claimant(chord, shortcuts) is None


def describe_claimant(chord: Chord, shortcuts: list[Shortcut]) -> str | None:
    """Inline message for the capture field, e.g. ``already: Noctalia``."""
    existing = claimant(chord, shortcuts)
    if existing is None:
        return None
    who = existing.owner or existing.label or "another binding"
    return f"already: {who}"


def first_free(candidates: list[Chord], shortcuts: list[Shortcut]) -> Chord | None:
    """Pick the first candidate chord nothing else has claimed.

    Used by the installer to choose this tool's own hotkey without stomping
    something the user (or Noctalia/DMS) already relies on.
    """
    for chord in candidates:
        if is_available(chord, shortcuts):
            return chord
    return None
