"""Load gtk4-layer-shell, correctly.

This module exists for one reason: **it must be imported before anything
imports ``gi.repository.Gtk``**. gtk4-layer-shell works by interposing on
libwayland-client's symbols, which only works if its shared object is in the
link map *before* libwayland-client is. Importing GTK first pulls in
libwayland-client, and from then on the interposition silently does nothing:
``LayerShell.is_supported()`` reports False, no error is raised, and the
overlay quietly degrades to an ordinary toplevel window -- which a tiling
compositor then tiles.

The upstream Python example (``examples/simple-example.py`` in
wmww/gtk4-layer-shell) does exactly this ``CDLL`` first for the same reason.

So: ``ui/overlay.py`` imports this module on its first line, and every other
module in this package gets GTK indirectly through it.
"""

from __future__ import annotations

from ctypes import CDLL

# Versioned soname first: that is what a distro package actually installs.
# The unversioned symlink only exists when the -dev/-devel package is present.
_SONAMES = ("libgtk4-layer-shell.so.0", "libgtk4-layer-shell.so")

_load_error: str | None = None
_loaded_soname: str | None = None

for _soname in _SONAMES:
    try:
        CDLL(_soname)
    except OSError as exc:  # noqa: PERF203 - the loop *is* the fallback chain
        _load_error = str(exc)
        continue
    _loaded_soname = _soname
    _load_error = None
    break

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")

LayerShell = None

if _loaded_soname is not None:
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402
    except (ValueError, ImportError) as exc:
        LayerShell = None
        _load_error = f"typelib unavailable: {exc}"

# Importing Gtk *after* the CDLL above is the whole point of this module.
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402,F401


def available() -> bool:
    """Whether layer-shell is usable for real, not merely importable.

    ``is_supported()`` is the honest check: it returns False when the library
    loaded but the compositor does not implement wlr-layer-shell, and also when
    the interposition failed because of load order.
    """
    if LayerShell is None:
        return False
    try:
        return bool(LayerShell.is_supported())
    except Exception:  # noqa: BLE001 - a broken binding must not crash the overlay
        return False


def status() -> str:
    """One-line explanation for ``cachy-shortcuts doctor``."""
    if LayerShell is None:
        return f"not loaded ({_load_error or 'library not found'})"
    if not available():
        return "loaded, but the compositor does not support wlr-layer-shell"
    return f"ok ({_loaded_soname})"
