"""MangoWM backend -- line-oriented ``bind=`` grammar.

    bind=SUPER,Return,spawn,st
    bind=NONE,XF86MonBrightnessUp,spawn,brightnessctl set +5%
    bindl=SUPER,l,quit

Grammar is ``bind[flags]=<MODS>,<KEY>,<COMMAND>,<ARGS>`` where ARGS may itself
contain commas, so the split is limited to three.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .. import APP_IDS, RULE_MARKER
from ..model import Chord, Shortcut, SourceRef, infer_category
from .base import Backend, FloatRule

_BIND_RE = re.compile(r"^(?P<kw>bind(?P<flags>[lsrp]*))\s*=\s*(?P<rest>.*)$")
_SOURCE_RE = re.compile(r"^source\s*=\s*(?P<path>.+?)\s*$")

_MANGO_MOD_SPELLING = {
    "super": "SUPER",
    "ctrl": "CTRL",
    "alt": "ALT",
    "shift": "SHIFT",
}

_MANGO_KEY_SPELLING = {
    "return": "Return",
    "escape": "Escape",
    "space": "space",
    "tab": "Tab",
    "backspace": "BackSpace",
    "delete": "Delete",
    "page_up": "Prior",
    "page_down": "Next",
    "slash": "slash",
    "minus": "minus",
    "equal": "equal",
    "bracketleft": "bracketleft",
    "bracketright": "bracketright",
    "print": "Print",
}


class MangoBackend(Backend):
    name = "mango"
    display_name = "MangoWM"

    def __init__(self, config_root: Path | None = None) -> None:
        self._root = config_root or (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mango"
        )

    def config_paths(self) -> list[Path]:
        main = self._root / "config.conf"
        if not main.exists():
            fallback = Path("/etc/mango/config.conf")
            main = fallback if fallback.exists() else main
        out: list[Path] = []
        self._collect(main, out, set())
        return out

    def _collect(self, path: Path, out: list[Path], visited: set[Path]) -> None:
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
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            m = _SOURCE_RE.match(stripped)
            if m:
                target = os.path.expanduser(m.group("path").strip())
                candidate = Path(target)
                if not candidate.is_absolute():
                    candidate = path.parent / candidate
                self._collect(candidate, out, visited)

    def parse(self, text: str, path: Path) -> list[Shortcut]:
        out: list[Shortcut] = []
        offset = 0
        for lineno, line in enumerate(text.splitlines(keepends=True), start=1):
            stripped = line.strip()
            content_len = len(line.rstrip("\n"))
            if not stripped or stripped.startswith("#"):
                offset += len(line)
                continue
            m = _BIND_RE.match(stripped)
            if not m:
                offset += len(line)
                continue
            parts = m.group("rest").split(",", 3)
            if len(parts) < 3:
                offset += len(line)
                continue
            mods_raw, key_raw, command = parts[0], parts[1], parts[2]
            args = parts[3] if len(parts) > 3 else ""
            try:
                chord = Chord.from_parts(mods_raw.split("+"), key_raw)
            except (KeyError, ValueError):
                offset += len(line)
                continue
            action = f"{command} {args}".strip()
            indent = len(line) - len(line.lstrip())
            out.append(
                Shortcut(
                    chord=chord,
                    action=action,
                    description="",
                    category=infer_category(action),
                    source=SourceRef(
                        backend=self.name,
                        path=path,
                        start=offset + indent,
                        end=offset + content_len,
                        line=lineno,
                    ),
                    raw=stripped,
                    extras={
                        "command": command,
                        "args": args,
                        "flags": m.group("flags") or "",
                        "mods_raw": mods_raw,
                        "key_raw": key_raw,
                    },
                )
            )
            offset += len(line)
        return out

    def render(
        self,
        chord: Chord,
        action: str,
        description: str = "",
        extras: dict | None = None,
    ) -> str:
        extras = extras or {}
        mods = "+".join(_MANGO_MOD_SPELLING.get(m, m.upper()) for m in chord.mods)
        if not mods:
            mods = "NONE"
        key = _MANGO_KEY_SPELLING.get(chord.key, chord.key)
        # Reuse the config's own spelling when this is still the same chord, so
        # editing one field doesn't reformat the others (notably code: forms).
        original_mods, original_key = extras.get("mods_raw"), extras.get("key_raw")
        if original_mods is not None and original_key is not None:
            try:
                if Chord.from_parts(original_mods.split("+"), original_key) == chord:
                    mods, key = original_mods, original_key
            except (KeyError, ValueError):
                pass
        text = action.strip()
        if " " in text:
            command, args = text.split(" ", 1)
        else:
            command, args = text, ""
        # Preserve bind flags (l/s/r/p) so a locked-screen bind stays one.
        keyword = "bind" + (extras.get("flags") or "")
        return f"{keyword}={mods},{key},{command},{args}"

    def insertion_point(self, text: str) -> tuple[int, str, str]:
        # Append after the final existing bind so new entries stay grouped.
        last_end = 0
        offset = 0
        for line in text.splitlines(keepends=True):
            if _BIND_RE.match(line.strip()):
                last_end = offset + len(line.rstrip("\n"))
            offset += len(line)
        if last_end:
            return (last_end, "\n", "")
        prefix = "" if text.endswith("\n") or not text else "\n"
        return (len(text), prefix, "\n")

    def float_rule(self) -> FloatRule | None:
        """``windowrule`` lines forcing the overlay to float.

        Mango takes one rule per line and matches a single appid each, so both
        spellings need their own line rather than one rule with alternatives.
        """
        rules = "\n".join(
            f"windowrule=isfloating:1,noblur:1,appid:{app_id}" for app_id in APP_IDS
        )
        body = f"# {RULE_MARKER}: keep the keybinding overlay out of the layout\n{rules}"
        paths = self.config_paths()
        target = paths[0] if paths else (self._root / "config.conf")
        return FloatRule(
            backend=self.name, path=target, body=body, marker=f"# {RULE_MARKER}:"
        )

    def reload(self) -> None:
        self._run(["mmsg", "-d", "reload_config"])

    def focused_window(self) -> str | None:
        out = self._run(["mmsg", "-g", "-c"])
        if not out:
            return None
        # mmsg emits `key: value` lines; appid is the useful one.
        for line in out.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            if key.strip().lower() in ("appid", "app_id", "class"):
                cleaned = value.strip().strip('"')
                if cleaned:
                    return cleaned
        return None
