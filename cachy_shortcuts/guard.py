"""Which chords are dangerous to bind: the ones with no real modifier.

A compositor binding is global. Bind a bare Down, a letter or Return and every
application stops receiving that key -- typing, navigating and confirming all
break at once, which is what rebinding a command to Down did. So a chord is
graded by how much it takes away, and the editor refuses it unless the caller
has confirmed that many times (``risk_ok``).

0 -- fine: carries Super/Ctrl/Alt, or is a media / Print key no app types.
1 -- confirm once: a bare key apps rarely depend on (F-keys, raw keycodes).
2 -- confirm twice: a bare or Shift-only key people type or navigate with.
"""

from __future__ import annotations

from .model import Chord
from .normalize import _KEY_ALIASES

# Shift, Caps and the X11 lock slots don't stop a key reaching the focused app,
# so they don't count as a modifier here.
_REAL_MODS = {"super", "ctrl", "alt"}

# Every canonical key an app can receive as typing or navigation; Print is the
# one alias target nothing types.
_TYPING_KEYS = frozenset(_KEY_ALIASES.values()) - {"print"}


def risk(chord: Chord) -> int:
    if _REAL_MODS.intersection(chord.mods):
        return 0
    key = chord.key
    if key.startswith("xf86") or key == "print":
        return 0
    if len(key) == 1 or key in _TYPING_KEYS:
        return 2
    return 1


def explain(chord: Chord) -> str:
    """Why ``chord`` is graded as it is, for an error or a form message."""
    level = risk(chord)
    name = chord.display()
    if level == 2:
        return f"{name} has no Super/Ctrl/Alt, so it would stop working as a normal key in every app"
    if level == 1:
        return f"{name} has no Super/Ctrl/Alt, so apps that use that key would lose it"
    return ""
