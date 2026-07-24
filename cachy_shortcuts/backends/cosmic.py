"""COSMIC backend -- RON shortcut map.

    {
        (modifiers: [Super, Shift], key: "Left"): Move(Left),
        (modifiers: [Super], key: "t"): Spawn("alacritty"),
    }

The user's ``custom`` file is layered over the system ``defaults`` file: an
entry in custom replaces the default for the same chord, and the ``Disable``
action removes a default binding entirely. Reading has to reproduce that merge
or the overlay would show bindings the compositor is not actually honouring.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..model import Chord, Shortcut, SourceRef, infer_category
from ._kdl import Scanner
from .base import Backend

_MODIFIERS_RE = re.compile(r"modifiers\s*:\s*\[([^\]]*)\]")
_KEY_RE = re.compile(r'key\s*:\s*"([^"]*)"')

_COSMIC_MOD_SPELLING = {
    "super": "Super",
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
}

_COSMIC_KEY_SPELLING = {
    "return": "Return",
    "escape": "Escape",
    "space": "space",
    "page_up": "Prior",
    "page_down": "Next",
    "print": "Print",
}


class CosmicBackend(Backend):
    name = "cosmic"
    display_name = "COSMIC"

    _RELATIVE = Path("cosmic/com.system76.CosmicSettings.Shortcuts/v1")

    def __init__(
        self, config_root: Path | None = None, system_root: Path | None = None
    ) -> None:
        base = config_root or Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        )
        self._custom = base / self._RELATIVE / "custom"
        self._defaults = (system_root or Path("/usr/share")) / self._RELATIVE / "defaults"

    def config_paths(self) -> list[Path]:
        # Custom first: it is the only writable one.
        return [self._custom, self._defaults]

    def read(self) -> list[Shortcut]:
        """Merge custom over defaults, honouring ``Disable``."""
        merged: dict[str, Shortcut] = {}
        for path in (self._defaults, self._custom):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for shortcut in self.parse(text, path):
                if shortcut.extras.get("action_value", "").strip().startswith(
                    ("Disable", "Disabled")
                ):
                    merged.pop(shortcut.chord.canonical, None)
                    continue
                merged[shortcut.chord.canonical] = shortcut
        return list(merged.values())

    def parse(self, text: str, path: Path) -> list[Shortcut]:
        sc = Scanner(text)
        out: list[Shortcut] = []
        i = text.find("{")
        if i == -1:
            return []
        i += 1
        end = text.rfind("}")
        if end == -1:
            end = len(text)
        while i < end:
            i = sc.skip_trivia(i)
            if i >= end or text[i] == "}":
                break
            if text[i] != "(":
                i += 1
                continue
            entry_start = i
            key_end = _match(text, sc, i, "(", ")")
            key_src = text[i:key_end]
            j = sc.skip_trivia(key_end)
            if j >= end or text[j] != ":":
                i = key_end
                continue
            j = sc.skip_trivia(j + 1)
            value_end = _scan_value(text, sc, j, end)
            value = text[j:value_end].strip().rstrip(",").strip()
            entry_end = value_end
            # Include a trailing comma in the span so deletion is clean.
            probe = sc.skip_trivia(value_end)
            if probe < end and text[probe] == ",":
                entry_end = probe + 1

            mods_match = _MODIFIERS_RE.search(key_src)
            key_match = _KEY_RE.search(key_src)
            mods = []
            if mods_match:
                mods = [m.strip() for m in mods_match.group(1).split(",") if m.strip()]
            if not key_match:
                # A modifiers-only binding (e.g. tap Super for the launcher)
                # has no key; there is nothing for us to display or compare.
                i = entry_end
                continue
            try:
                chord = Chord.from_parts(mods, key_match.group(1))
            except (KeyError, ValueError):
                i = entry_end
                continue
            description = _humanize(value)
            out.append(
                Shortcut(
                    chord=chord,
                    action=value,
                    description=description,
                    category=infer_category(value, description),
                    source=SourceRef(
                        backend=self.name,
                        path=path,
                        start=entry_start,
                        end=entry_end,
                        line=sc.line_of(entry_start),
                    ),
                    raw=text[entry_start:entry_end],
                    extras={
                        "action_value": value,
                        "readonly": path == self._defaults,
                    },
                )
            )
            i = entry_end
        return out

    def render(
        self,
        chord: Chord,
        action: str,
        description: str = "",
        extras: dict | None = None,
    ) -> str:
        mods = ", ".join(
            _COSMIC_MOD_SPELLING.get(m, m.capitalize()) for m in chord.mods
        )
        key = _COSMIC_KEY_SPELLING.get(chord.key, chord.key)
        value = action.strip()
        # A bare command means "run this"; wrap it in the Spawn action.
        if not re.match(r"^[A-Z][A-Za-z]*(\(|$)", value):
            value = f'Spawn("{_escape(value)}")'
        return f'(modifiers: [{mods}], key: "{key}"): {value},'

    def insertion_point(self, text: str) -> tuple[int, str, str]:
        close = text.rfind("}")
        if close == -1:
            return (len(text), "{\n    ", "\n}\n")
        head = text[:close].rstrip()
        return (len(head), "\n    ", "\n")

    def write_target(self) -> Path:
        """COSMIC edits always go to the user's custom file, never defaults."""
        return self._custom

    def reload(self) -> None:
        # cosmic-settings-daemon watches the config; nothing to trigger.
        return None


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _match(text: str, sc: Scanner, i: int, open_ch: str, close_ch: str) -> int:
    depth = 0
    while i < len(text):
        if sc.at_string(i):
            i = sc.skip_string(i)
            continue
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _scan_value(text: str, sc: Scanner, i: int, end: int) -> int:
    """Read a RON value, stopping at the comma that ends it."""
    depth = 0
    while i < end:
        if sc.at_string(i):
            i = sc.skip_string(i)
            continue
        c = text[i]
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "," and depth == 0:
            return i
        elif c == "\n" and depth == 0:
            return i
        elif c == "}" and depth == 0:
            return i
        i += 1
    return end


_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _humanize(value: str) -> str:
    """Turn ``Move(Left)`` into ``Move left``, ``Spawn("foo")`` into ``foo``."""
    value = value.strip()
    m = re.match(r"^(\w+)\((.*)\)$", value, re.DOTALL)
    if not m:
        return _CAMEL_RE.sub(" ", value).capitalize()
    outer, inner = m.group(1), m.group(2).strip()
    if outer == "Spawn":
        return inner.strip('"')
    inner_words = _CAMEL_RE.sub(" ", inner.strip('"')).lower()
    if outer == "System":
        return inner_words.capitalize()
    outer_words = _CAMEL_RE.sub(" ", outer)
    return f"{outer_words} {inner_words}".strip()
