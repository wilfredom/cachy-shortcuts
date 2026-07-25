"""Scan installed applications from XDG .desktop entries.

Used by the edit UI so binding a new app is a type-ahead pick rather than
remembering an exec path and its flags.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

# Field codes a .desktop Exec line may contain; they are placeholders for
# files/URLs the launcher would substitute, and are meaningless in a keybind.
_FIELD_CODES = re.compile(r"%[fFuUdDnNickvm]")


@dataclass(frozen=True)
class DesktopApp:
    name: str
    command: str
    desktop_id: str
    icon: str = ""
    categories: tuple[str, ...] = ()

    def matches(self, query: str) -> bool:
        q = query.strip().lower()
        if not q:
            return True
        return q in self.name.lower() or q in self.command.lower()


def application_dirs() -> list[Path]:
    dirs: list[Path] = []
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    for base in [data_home, *raw.split(":")]:
        if not base:
            continue
        candidate = Path(base) / "applications"
        if candidate.is_dir() and candidate not in dirs:
            dirs.append(candidate)
    return dirs


def _parse_desktop(path: Path) -> DesktopApp | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    in_entry = False
    fields: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            # Only the main entry matters; skip "Desktop Action ..." groups.
            in_entry = stripped == "[Desktop Entry]"
            continue
        if not in_entry or "=" not in stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        # Ignore localised variants like Name[de]; we want the plain key.
        if "[" in key:
            continue
        fields.setdefault(key, value.strip())

    if fields.get("Type", "Application") != "Application":
        return None
    if fields.get("NoDisplay", "").lower() == "true":
        return None
    if fields.get("Hidden", "").lower() == "true":
        return None
    name = fields.get("Name")
    exec_line = fields.get("Exec")
    if not name or not exec_line:
        return None

    command = _FIELD_CODES.sub("", exec_line).strip()
    command = re.sub(r"\s{2,}", " ", command)
    if not command:
        return None
    if fields.get("Terminal", "").lower() == "true":
        # Without this the app would launch with no visible window.
        command = f"xterm -e {command}"

    categories = tuple(
        c for c in fields.get("Categories", "").split(";") if c
    )
    return DesktopApp(
        name=name,
        command=command,
        desktop_id=path.stem,
        icon=fields.get("Icon", ""),
        categories=categories,
    )


def scan() -> list[DesktopApp]:
    """All visible installed applications, de-duplicated by desktop id.

    Earlier directories win, matching XDG precedence -- a user's override in
    ~/.local/share shadows the system copy.
    """
    seen: dict[str, DesktopApp] = {}
    for directory in application_dirs():
        for entry in sorted(directory.glob("*.desktop")):
            if entry.stem in seen:
                continue
            app = _parse_desktop(entry)
            if app is not None:
                seen[entry.stem] = app
    return sorted(seen.values(), key=lambda a: a.name.lower())


def rank(apps: list[DesktopApp], query: str, limit: int = 20) -> list[DesktopApp]:
    """Filter and order ``apps`` by how well they match ``query``.

    Split out from ``search`` so a type-ahead can scan the disk once and then
    re-rank a cached list on every keystroke, instead of re-globbing every
    applications directory per character typed.
    """
    matched = [a for a in apps if a.matches(query)]
    q = query.strip().lower()

    def key(app: DesktopApp) -> tuple[int, str]:
        name = app.name.lower()
        if name == q:
            return (0, name)
        if name.startswith(q):
            return (1, name)
        return (2, name)

    matched.sort(key=key)
    return matched[:limit]


def search(query: str, limit: int = 20) -> list[DesktopApp]:
    return rank(scan(), query, limit)


def command_for(name: str) -> str | None:
    """Resolve a human app name to a runnable command, if it is installed."""
    for app in scan():
        if app.name.lower() == name.strip().lower():
            return app.command
    return None


def quote(command: str) -> str:
    """Quote a command for embedding in a config file."""
    parts = shlex.split(command)
    return " ".join(shlex.quote(p) for p in parts) if parts else command
