"""Work out which compositor is running, and which are merely installed.

Detection is layered: environment variables first (cheap and definitive when
present), then a scan of running process names, then finally the existence of
a config file. The last one is only good enough to say "installed", never
"active" -- the user has all three on disk.
"""

from __future__ import annotations

import os
from pathlib import Path

from .backends import Backend, CosmicBackend, MangoBackend, NiriBackend

# Process name each compositor runs under.
_PROCESS_NAMES = {
    "niri": ("niri",),
    "cosmic": ("cosmic-comp",),
    "mango": ("mango", "mangowc"),
}

# Substrings that identify a session via XDG_CURRENT_DESKTOP and friends.
_DESKTOP_HINTS = {
    "niri": ("niri",),
    "cosmic": ("cosmic",),
    "mango": ("mango",),
}


def _desktop_env() -> str:
    parts = [
        os.environ.get("XDG_CURRENT_DESKTOP", ""),
        os.environ.get("XDG_SESSION_DESKTOP", ""),
        os.environ.get("DESKTOP_SESSION", ""),
    ]
    return ":".join(parts).lower()


def _running_processes() -> set[str]:
    """Process names currently running, read straight from /proc."""
    names: set[str] = set()
    proc = Path("/proc")
    if not proc.is_dir():
        return names
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            names.add((entry / "comm").read_text().strip())
        except OSError:
            continue
    return names


def active_backend_name() -> str | None:
    """Name of the compositor that is currently running this session."""
    # Definitive per-compositor environment markers.
    if os.environ.get("NIRI_SOCKET"):
        return "niri"

    desktop = _desktop_env()
    for name, hints in _DESKTOP_HINTS.items():
        if any(hint in desktop for hint in hints):
            return name

    running = _running_processes()
    for name, candidates in _PROCESS_NAMES.items():
        if any(c in running for c in candidates):
            return name
    return None


def backend_by_name(name: str) -> Backend | None:
    for cls in (NiriBackend, CosmicBackend, MangoBackend):
        if cls.name == name:
            return cls()
    return None


def detect_active() -> Backend | None:
    name = active_backend_name()
    return backend_by_name(name) if name else None


def detect_installed() -> list[Backend]:
    """Every backend with a config on disk, active or not.

    The user runs all three, so the CLI defaults to showing everything it can
    find rather than only the live session.
    """
    return [b for b in (NiriBackend(), CosmicBackend(), MangoBackend()) if b.is_installed()]


def detect_all() -> list[Backend]:
    return [NiriBackend(), CosmicBackend(), MangoBackend()]
