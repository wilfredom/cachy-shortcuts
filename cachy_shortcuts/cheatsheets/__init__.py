"""Bundled and user-defined per-app cheat sheets.

An OS-level chord like Super+Return is compositor config, discovered by the
backend readers. An app's own shortcuts (Firefox's Ctrl+T, VS Code's
Ctrl+Shift+P, ...) live in that app's own config, which this tool has no
business touching. Cheat sheets are purely informational: they surface those
bindings in the same overlay, grouped under Category.APP and keyed off
whatever app was focused before the overlay opened.

Packs are plain YAML so anyone can add or override one without touching code.
PyYAML is used when available; otherwise a small parser covers exactly the
flat schema these packs use, so the feature works with zero extra dependency.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..model import Category, Chord, Shortcut

_PACKS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Pack:
    name: str
    match: tuple[str, ...]
    entries: tuple[tuple[str, str], ...]  # (chord_text, description)
    source: Path | None = None

    def matches(self, app_id: str) -> bool:
        needle = app_id.lower()
        return any(m.lower() in needle for m in self.match)


def user_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "cachy-shortcuts" / "cheatsheets"


# --- YAML loading, with a dependency-free fallback -------------------------


def _try_real_yaml(text: str):
    try:
        import yaml
    except ImportError:
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        # Malformed pack: fall through to the minimal parser (which will
        # also fail gracefully) rather than raising through to the caller.
        return None


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _minimal_yaml_load(text: str) -> dict:
    """Parse the restricted subset of YAML a cheat-sheet pack uses.

    Supports top-level ``key: value`` scalars, a ``match:`` block sequence of
    ``- item`` lines, and a ``shortcuts:`` block sequence of two-key mappings
    (``- chord: ...`` / ``  description: ...``). No flow style, anchors, or
    multi-document files -- packs are hand-written to fit this shape, so this
    covers them without pulling in a real YAML dependency.
    """
    lines = [
        ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")
    ]
    data: dict = {}
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("-") or ":" not in stripped:
            i += 1
            continue
        key, _, rest = stripped.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest:
            data[key] = _strip_quotes(rest)
            i += 1
            continue
        # A bare "key:" introduces a block sequence.
        items: list = []
        i += 1
        while i < len(lines) and lines[i].strip().startswith("-"):
            item_line = lines[i].strip()[1:].strip()
            if ":" in item_line:
                entry: dict = {}
                k, _, v = item_line.partition(":")
                entry[k.strip()] = _strip_quotes(v)
                i += 1
                while (
                    i < len(lines)
                    and not lines[i].strip().startswith("-")
                    and ":" in lines[i]
                ):
                    k2, _, v2 = lines[i].strip().partition(":")
                    entry[k2.strip()] = _strip_quotes(v2)
                    i += 1
                items.append(entry)
            else:
                items.append(_strip_quotes(item_line))
                i += 1
        data[key] = items
    return data


def _load_pack(path: Path) -> Pack | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    data = _try_real_yaml(text)
    if data is None:
        try:
            data = _minimal_yaml_load(text)
        except Exception:  # noqa: BLE001 - a malformed pack is skipped, not fatal
            return None
    if not isinstance(data, dict):
        return None

    name = data.get("name") or path.stem
    match = tuple(data.get("match") or [path.stem])
    raw_entries = data.get("shortcuts") or []
    entries = tuple(
        (e.get("chord", ""), e.get("description", ""))
        for e in raw_entries
        if isinstance(e, dict) and e.get("chord")
    )
    if not entries:
        return None
    return Pack(name=name, match=match, entries=entries, source=path)


def _iter_pack_files():
    """User packs first: a same-named file overrides a bundled pack outright,
    and users can add packs for apps that aren't bundled at all."""
    seen_stems: set[str] = set()
    directory = user_dir()
    if directory.is_dir():
        for path in sorted(directory.glob("*.yaml")):
            seen_stems.add(path.stem)
            yield path
    if _PACKS_DIR.is_dir():
        for path in sorted(_PACKS_DIR.glob("*.yaml")):
            if path.stem in seen_stems:
                continue
            yield path


def available_packs() -> list[Pack]:
    return [p for p in (_load_pack(f) for f in _iter_pack_files()) if p is not None]


def load_for(app_id: str | None) -> list[Shortcut]:
    """App-specific reference shortcuts for the given focused app, if any.

    These are read-only display entries (``source=None``): they document what
    an app's own config already binds, not compositor config this tool can
    edit. ``editor.py``'s edit functions already refuse anything with no
    source span, so this alone keeps them from being silently "edited" into
    a write that would go nowhere.
    """
    if not app_id:
        return []
    for pack in available_packs():
        if not pack.matches(app_id):
            continue
        out: list[Shortcut] = []
        for chord_text, description in pack.entries:
            try:
                chord = Chord.parse(chord_text)
            except (KeyError, ValueError):
                continue
            out.append(
                Shortcut(
                    chord=chord,
                    action=description,
                    description=description,
                    category=Category.APP,
                    source=None,
                    extras={"pack": pack.name, "readonly": True},
                )
            )
        return out  # first matching pack wins; never mix two apps' entries
    return []
