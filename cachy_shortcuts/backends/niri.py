"""Niri backend -- KDL ``binds { }`` block.

Niri watches every included config file and live-reloads on change, so writes
here take effect immediately with no reload step.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .. import APP_IDS, RULE_MARKER
from ..model import Category, Chord, Shortcut, SourceRef, infer_category
from ._kdl import Scanner, find_block
from .base import Backend, FloatRule

# Canonical key -> the spelling niri uses. Only needed when *emitting* a new
# bind; parsed binds keep their original spelling in extras so a round-trip is
# byte-identical.
_NIRI_KEY_SPELLING: dict[str, str] = {
    "return": "Return",
    "escape": "Escape",
    "space": "Space",
    "tab": "Tab",
    "backspace": "BackSpace",
    "delete": "Delete",
    "insert": "Insert",
    "slash": "Slash",
    "backslash": "Backslash",
    "bracketleft": "BracketLeft",
    "bracketright": "BracketRight",
    "minus": "Minus",
    "equal": "Equal",
    "comma": "Comma",
    "period": "Period",
    "semicolon": "Semicolon",
    "apostrophe": "Apostrophe",
    "grave": "Grave",
    "home": "Home",
    "end": "End",
    "page_up": "Page_Up",
    "page_down": "Page_Down",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "print": "Print",
}

_NIRI_MOD_SPELLING: dict[str, str] = {
    "super": "Mod",
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
}

_XF86_SPELLING: dict[str, str] = {
    "xf86audioraisevolume": "XF86AudioRaiseVolume",
    "xf86audiolowervolume": "XF86AudioLowerVolume",
    "xf86audiomute": "XF86AudioMute",
    "xf86audiomicmute": "XF86AudioMicMute",
    "xf86audioplay": "XF86AudioPlay",
    "xf86audiopause": "XF86AudioPause",
    "xf86audionext": "XF86AudioNext",
    "xf86audioprev": "XF86AudioPrev",
    "xf86monbrightnessup": "XF86MonBrightnessUp",
    "xf86monbrightnessdown": "XF86MonBrightnessDown",
}


def niri_key_spelling(key: str) -> str:
    if key in _NIRI_KEY_SPELLING:
        return _NIRI_KEY_SPELLING[key]
    if key in _XF86_SPELLING:
        return _XF86_SPELLING[key]
    if len(key) == 1:
        return key.upper()
    return key.capitalize()


def niri_chord_spelling(chord: Chord) -> str:
    parts = [_NIRI_MOD_SPELLING.get(m, m.capitalize()) for m in chord.mods]
    parts.append(niri_key_spelling(chord.key))
    return "+".join(parts)


class NiriBackend(Backend):
    name = "niri"
    display_name = "Niri"

    def __init__(self, config_root: Path | None = None) -> None:
        self._root = config_root or (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "niri"
        )

    # --- discovery ---------------------------------------------------------

    def config_paths(self) -> list[Path]:
        """Main config first, then every file it includes, depth-first.

        The main config comes first because that is where a brand-new binding
        goes when no ``binds`` block exists anywhere else.
        """
        main = self._root / "config.kdl"
        seen: list[Path] = []
        self._collect_includes(main, seen, set())
        # Put whichever file actually owns a binds block first, so new bindings
        # land next to the existing ones rather than in an unrelated file.
        with_binds = [p for p in seen if self._has_binds(p)]
        without = [p for p in seen if p not in with_binds]
        return with_binds + without

    def _has_binds(self, path: Path) -> bool:
        try:
            return find_block(path.read_text(encoding="utf-8"), "binds") is not None
        except (OSError, UnicodeDecodeError):
            return False

    def _collect_includes(
        self, path: Path, out: list[Path], visited: set[Path]
    ) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved in visited or not path.exists():
            return
        visited.add(resolved)
        out.append(path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        for target in self._parse_includes(text):
            self._collect_includes((path.parent / target), out, visited)

    @staticmethod
    def _parse_includes(text: str) -> list[str]:
        """Find ``include "x.kdl"`` and ``include optional=true "x.kdl"``."""
        sc = Scanner(text)
        found: list[str] = []
        i = 0
        while i < sc.n:
            i = sc.skip_trivia(i)
            if i >= sc.n:
                break
            if sc.at_string(i):
                i = sc.skip_string(i)
                continue
            if not text.startswith("include", i):
                # Advance past this node.
                if text[i] == "{":
                    i = sc.match_brace(i)
                    continue
                i += 1
                continue
            j = i + len("include")
            # Consume the rest of the line, picking up the last string literal
            # (the path) and ignoring any optional=true property.
            last: str | None = None
            while j < sc.n and text[j] != "\n":
                j = sc.skip_trivia(j, stop_at_newline=True)
                if j >= sc.n or text[j] == "\n":
                    break
                if sc.at_string(j):
                    end = sc.skip_string(j)
                    last = _unquote(text[j:end])
                    j = end
                    continue
                j += 1
            if last:
                found.append(last)
            i = j
        return found

    # --- parsing -----------------------------------------------------------

    def parse(self, text: str, path: Path) -> list[Shortcut]:
        block = find_block(text, "binds")
        if block is None:
            return []
        body_start, body_end, _ = block
        sc = Scanner(text)
        out: list[Shortcut] = []
        i = body_start
        while i < body_end:
            i = sc.skip_trivia(i)
            if i >= body_end:
                break
            node_start = i
            disabled = False
            if text.startswith("/-", i):
                disabled = True
                i = sc.skip_trivia(i + 2)
            # Chord token
            tok_start = i
            while i < body_end and not text[i].isspace() and text[i] not in "{};":
                i += 1
            chord_text = text[tok_start:i]
            if not chord_text:
                i += 1
                continue
            # Properties up to the body brace or end of node
            props: dict[str, str] = {}
            while i < body_end:
                i = sc.skip_trivia(i)
                if i >= body_end or text[i] in "{};":
                    break
                key_start = i
                while i < body_end and text[i] not in "= \t\n{};":
                    i += 1
                prop_key = text[key_start:i]
                if i < body_end and text[i] == "=":
                    i += 1
                    if sc.at_string(i):
                        end = sc.skip_string(i)
                        props[prop_key] = _unquote(text[i:end])
                        i = end
                    else:
                        val_start = i
                        while i < body_end and not text[i].isspace() and text[i] not in "{};":
                            i += 1
                        props[prop_key] = text[val_start:i]
                elif not prop_key:
                    i += 1
            action = ""
            if i < body_end and text[i] == "{":
                brace_end = sc.match_brace(i)
                action = _clean_body(text[i + 1 : brace_end - 1])
                i = brace_end
            node_end = i
            try:
                chord = Chord.parse(chord_text)
            except (KeyError, ValueError):
                # Unknown modifier or malformed chord: skip rather than
                # inventing a chord that would compare equal to something else.
                continue
            title = props.get("hotkey-overlay-title")
            description = "" if title in (None, "null") else title
            extras = {
                "props": props,
                "spelling": chord_text,
                "disabled": disabled,
                "title_null": title == "null",
            }
            out.append(
                Shortcut(
                    chord=chord,
                    action=action,
                    description=description,
                    category=infer_category(action, description),
                    source=SourceRef(
                        backend=self.name,
                        path=path,
                        start=node_start,
                        end=node_end,
                        line=sc.line_of(node_start),
                    ),
                    raw=text[node_start:node_end],
                    extras=extras,
                )
            )
        return out

    # --- writing -----------------------------------------------------------

    def render(
        self,
        chord: Chord,
        action: str,
        description: str = "",
        extras: dict | None = None,
    ) -> str:
        extras = extras or {}
        props = dict(extras.get("props") or {})
        parts = [_preferred_spelling(chord, extras.get("spelling"))]
        # Rebuild properties in their original order so an edit to one field
        # doesn't reshuffle the rest of the line.
        emitted_title = False
        for key, value in props.items():
            if key == "hotkey-overlay-title":
                emitted_title = True
                if description:
                    parts.append(f'hotkey-overlay-title="{_escape(description)}"')
                elif extras.get("title_null"):
                    # `null` is not the same as absent: it hides the binding
                    # from niri's own hotkey overlay. Dropping it would make
                    # hidden bindings reappear there.
                    parts.append("hotkey-overlay-title=null")
                continue
            parts.append(f"{key}={_render_prop(value)}")
        if description and not emitted_title:
            parts.insert(1, f'hotkey-overlay-title="{_escape(description)}"')
        body = action.strip().rstrip(";")
        prefix = "/-" if extras.get("disabled") else ""
        return f"{prefix}{' '.join(parts)} {{ {body}; }}"

    def insertion_point(self, text: str) -> tuple[int, str, str]:
        block = find_block(text, "binds")
        if block is None:
            # No binds block: append one at end of file.
            prefix = "\n\nbinds {\n    "
            return (len(text), prefix, "\n}\n")
        _, body_end, close = block
        indent = _detect_indent(text, body_end)
        return (body_end, indent, "\n")

    # --- tiling exception ---------------------------------------------------

    def float_rule(self) -> FloatRule | None:
        """A ``window-rule`` opening the overlay floating rather than tiled.

        Both app-id spellings are matched: niri takes several ``match`` lines
        per rule and treats them as alternatives, so one rule covers the
        overlay whether GTK reports its application id or the binary name.
        """
        matches = "\n".join(
            f"    match app-id={_kdl_regex(f'^{_escape_regex(app_id)}$')}"
            for app_id in APP_IDS
        )
        body = (
            f"// {RULE_MARKER}: keep the keybinding overlay out of the layout\n"
            "window-rule {\n"
            f"{matches}\n"
            "    open-floating true\n"
            "    open-focused true\n"
            "}"
        )
        # The main config, not config_paths()[0]: that one is ordered so new
        # *bindings* land beside existing ones, which for an include-heavy
        # setup is some keybinds-only file. A window rule belongs in the config
        # proper.
        main = self._root / "config.kdl"
        paths = self.config_paths()
        target = main if main.exists() or not paths else paths[0]
        return FloatRule(
            backend=self.name, path=target, body=body, marker=f"// {RULE_MARKER}:"
        )

    # --- runtime -----------------------------------------------------------

    def reload(self) -> None:
        # Niri watches its config files and reloads automatically; nothing to do.
        return None

    def focused_window(self) -> str | None:
        out = self._run(["niri", "msg", "--json", "focused-window"])
        if not out:
            return None
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict):
            return data.get("app_id") or data.get("title")
        return None


def _unquote(literal: str) -> str:
    s = literal.strip()
    if s.startswith("r"):
        j = 1
        while j < len(s) and s[j] == "#":
            j += 1
        if j < len(s) and s[j] == '"':
            hashes = j - 1
            return s[j + 1 : len(s) - 1 - hashes]
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return (
            s[1:-1]
            .replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\\\", "\\")
        )
    return s


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


_REGEX_META = re.compile(r"([.^$*+?()\[\]{}|\\])")


def _escape_regex(s: str) -> str:
    """Escape regex metacharacters for niri's Rust-regex matchers.

    Narrower than ``re.escape``, which also escapes ``-`` and whitespace --
    legal in Rust's regex crate but noise in a config file a human reads.
    """
    return _REGEX_META.sub(r"\\\1", s)


def _kdl_regex(pattern: str) -> str:
    """Wrap a regex in KDL's raw-string form, as niri's own config does.

    Raw strings are what keep backslashes in ``dev\\.cachyos\\.Shortcuts``
    from being eaten by KDL's own escape processing before the regex ever
    sees them.
    """
    return f'r#"{pattern}"#'


def _preferred_spelling(chord: Chord, original: str | None) -> str:
    """Reuse the config's own spelling when the chord is unchanged.

    Niri accepts many spellings of the same key and our canonical form cannot
    reproduce camel-case names like ``WheelScrollDown``. Keeping the original
    text whenever it still denotes this chord makes edits to *other* fields
    byte-identical instead of quietly reformatting the user's config.
    """
    if original:
        try:
            if Chord.parse(original) == chord:
                return original
        except (KeyError, ValueError):
            pass
    return niri_chord_spelling(chord)


def _render_prop(value: str) -> str:
    """Bare for keywords and numbers, quoted for anything else."""
    if value in ("true", "false", "null"):
        return value
    try:
        float(value)
    except ValueError:
        return f'"{_escape(value)}"'
    return value


def _clean_body(body: str) -> str:
    """Collapse a bind body to a single action string."""
    lines = [ln.strip() for ln in body.strip().splitlines()]
    joined = " ".join(ln for ln in lines if ln and not ln.startswith("//"))
    return joined.strip().rstrip(";").strip()


def _detect_indent(text: str, body_end: int) -> str:
    """Match the indentation of the last binding in the block."""
    tail = text[:body_end].rstrip()
    line_start = tail.rfind("\n") + 1
    last_line = tail[line_start:]
    indent = last_line[: len(last_line) - len(last_line.lstrip())]
    return "\n" + (indent or "    ")
