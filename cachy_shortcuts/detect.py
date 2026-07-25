"""Work out which compositor is running, and which are merely installed.

Detection is layered: environment variables first (cheap and definitive when
present), then a scan of running process names, then finally the existence of
a config file. The last one is only good enough to say "installed", never
"active" -- the user has several of them on disk.
"""

from __future__ import annotations

import os
from pathlib import Path

from .backends import ALL_BACKENDS, Backend

# Process name each compositor runs under.
_PROCESS_NAMES = {
    "niri": ("niri",),
    "hyprland": ("Hyprland", "hyprland"),
    "cosmic": ("cosmic-comp",),
    "mango": ("mango", "mangowc"),
}

# Substrings that identify a session via XDG_CURRENT_DESKTOP and friends.
_DESKTOP_HINTS = {
    "niri": ("niri",),
    "hyprland": ("hyprland",),
    "cosmic": ("cosmic",),
    "mango": ("mango",),
}

# Environment variables only ever set by one compositor's own session.
_SESSION_MARKERS = {
    "NIRI_SOCKET": "niri",
    "HYPRLAND_INSTANCE_SIGNATURE": "hyprland",
}


def _desktop_env() -> str:
    parts = [
        os.environ.get("XDG_CURRENT_DESKTOP", ""),
        os.environ.get("XDG_SESSION_DESKTOP", ""),
        os.environ.get("DESKTOP_SESSION", ""),
    ]
    return ":".join(parts).lower()


def running_processes() -> set[str]:
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
    for variable, backend in _SESSION_MARKERS.items():
        if os.environ.get(variable):
            return backend

    desktop = _desktop_env()
    for name, hints in _DESKTOP_HINTS.items():
        if any(hint in desktop for hint in hints):
            return name

    running = running_processes()
    for name, candidates in _PROCESS_NAMES.items():
        if any(c in running for c in candidates):
            return name
    return None


def backend_by_name(name: str) -> Backend | None:
    for cls in ALL_BACKENDS:
        if cls.name == name:
            return cls()
    return None


def detect_active() -> Backend | None:
    name = active_backend_name()
    return backend_by_name(name) if name else None


def detect_installed() -> list[Backend]:
    """Every backend with a config on disk, active or not.

    The user runs several compositors, so the CLI defaults to showing
    everything it can find rather than only the live session.
    """
    return [b for b in detect_all() if b.is_installed()]


def detect_all() -> list[Backend]:
    return [cls() for cls in ALL_BACKENDS]
