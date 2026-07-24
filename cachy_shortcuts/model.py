"""Backend-agnostic shortcut records.

Everything above the backend layer speaks only these types. A ``Shortcut``
knows where it came from (file + byte span) so edits can be surgical: we
replace exactly the bytes the binding occupies and leave the user's comments,
ordering and hand-tuned formatting untouched.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .normalize import display_key, normalize_key, normalize_mod, order_mods


class Category(str, Enum):
    """Display grouping. Order here is the order sections render in."""

    LAUNCH = "Launch"
    WINDOWS = "Windows"
    WORKSPACES = "Workspaces"
    MEDIA = "Media"
    SCREENSHOT = "Screenshot"
    SYSTEM = "System"
    APP = "App"
    OTHER = "Other"


@dataclass(frozen=True)
class Chord:
    """A physical key combination, canonicalised across dialects."""

    mods: tuple[str, ...]
    key: str

    @classmethod
    def from_parts(cls, mods, key: str) -> "Chord":
        resolved: set[str] = set()
        for raw in mods:
            norm = normalize_mod(raw)
            if norm is not None:
                resolved.add(norm)
        return cls(mods=order_mods(resolved), key=normalize_key(key))

    @classmethod
    def parse(cls, text: str) -> "Chord":
        """Parse a ``Mod+Shift+Slash`` style string (niri, and human input)."""
        parts = [p for p in text.split("+") if p.strip()]
        if not parts:
            raise ValueError(f"empty chord: {text!r}")
        return cls.from_parts(parts[:-1], parts[-1])

    @property
    def canonical(self) -> str:
        """Stable identity string. This is what conflict detection compares."""
        return "+".join((*self.mods, self.key))

    def display(self) -> str:
        """Omarchy convention: modifiers space-joined, ``+`` before the key."""
        key = display_key(self.key)
        if not self.mods:
            return key
        return f"{' '.join(m.upper() for m in self.mods)} + {key}"

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.display()


@dataclass(frozen=True)
class SourceRef:
    """Exactly which bytes of which file produced a shortcut."""

    backend: str
    path: Path
    start: int
    end: int
    line: int

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass
class Shortcut:
    chord: Chord
    action: str
    description: str = ""
    category: Category = Category.OTHER
    source: SourceRef | None = None
    raw: str = ""
    # Backend-specific bits that must survive a round-trip untouched
    # (niri's allow-when-locked / cooldown-ms, mango's bind flags, ...).
    extras: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        """What the right-hand column shows."""
        return self.description or describe_action(self.action)

    @property
    def owner(self) -> str | None:
        """Which shell owns this bind, if it's obviously a shell's own hotkey.

        Used so the conflict detector can say *whose* chord it is instead of
        just reporting a collision.
        """
        act = self.action.lower()
        if "noctalia-shell" in act or "qs -c noctalia" in act:
            return "Noctalia"
        if re.search(r"\bdms\b\s+ipc", act) or "dankmaterialshell" in act:
            return "DMS"
        if "cachy-shortcuts" in act:
            return "cachy-shortcuts"
        return None


# --- category inference ----------------------------------------------------
#
# Ordered most-specific first: a screenshot bind usually also spawns something,
# and a volume bind is media before it is system, so first match wins.

_CATEGORY_RULES: tuple[tuple[Category, re.Pattern], ...] = (
    (
        Category.SCREENSHOT,
        re.compile(r"screenshot|screencast|grim|slurp|hyprshot|flameshot|satty"),
    ),
    (
        Category.MEDIA,
        re.compile(
            r"xf86audio|xf86monbrightness|playerctl|pamixer|wpctl|brightnessctl"
            r"|volume|mute|brightness|mpc\b"
        ),
    ),
    (
        Category.WORKSPACES,
        re.compile(r"workspace|\btag\b|viewtag|monitor|output|overview|\bview\b"),
    ),
    (
        Category.WINDOWS,
        re.compile(
            r"killclient|closewindow|\bclose\b|fullscreen|maximize|minimize"
            r"|floating|tiling|togglefloating|focusdir|focuswindow|movewindow"
            r"|resiz|swapwindow|\bfocus\b|\bmove\b|column|consume|expel|stack"
        ),
    ),
    (
        Category.SYSTEM,
        re.compile(
            r"\bquit\b|\bexit\b|logout|log-out|poweroff|shutdown|reboot|suspend"
            r"|lock|loginctl|systemctl|reload|reload_config|powermenu|settings"
            r"|control ?cent(er|re)|session|hotkey-overlay|cachy-shortcuts"
        ),
    ),
    (Category.LAUNCH, re.compile(r"spawn|exec|launch|\brun\b|terminal|browser")),
)


def infer_category(action: str, description: str = "") -> Category:
    haystack = f"{action} {description}".lower()
    for category, pattern in _CATEGORY_RULES:
        if pattern.search(haystack):
            return category
    return Category.OTHER


# Longest first, so "spawn-sh" is not truncated to "spawn".
_EXEC_PREFIXES = ("spawn_shell", "spawn-sh", "spawn", "exec-once", "exec")

_LABEL_MAX = 52


def _strip_exec_prefix(text: str) -> str:
    lowered = text.lower()
    for prefix in _EXEC_PREFIXES:
        if not lowered.startswith(prefix):
            continue
        # Require a boundary so "spawner" isn't mistaken for "spawn".
        rest = text[len(prefix) :]
        if rest and not rest[0].isspace() and rest[0] not in "\"',":
            continue
        stripped = rest.strip().lstrip(",").strip()
        if stripped:
            return stripped
    return text


def describe_action(action: str) -> str:
    """Best-effort human label for a bind that carries no explicit title.

    Niri writes each argument as its own quoted token
    (``spawn "wpctl" "set-volume" "5%+"``), so the label has to be tokenised
    rather than string-stripped, or the quotes end up in the middle of it.
    """
    text = _strip_exec_prefix(action.strip())
    if not text:
        return action.strip()
    try:
        parts = shlex.split(text)
    except ValueError:
        # Unbalanced quotes (a shell snippet); fall back to naive splitting.
        parts = text.replace('"', " ").split()
    if not parts:
        return text
    # A full path is noise; the binary name carries the meaning.
    parts[0] = parts[0].rsplit("/", 1)[-1]
    joined = " ".join(parts)
    if len(joined) <= _LABEL_MAX:
        return joined
    return joined[: _LABEL_MAX - 1].rstrip() + "…"
