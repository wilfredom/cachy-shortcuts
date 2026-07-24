"""Read the running shell's colours so the overlay looks native.

Under Niri the user runs Noctalia; under Mango, DankMaterialShell. Both keep a
palette on disk. We read it rather than shipping a fixed theme, so the overlay
matches whatever scheme is active instead of clashing with it.

Palette discovery is best-effort by design: these are third-party shells whose
config layouts move between versions, and a wrong colour is a cosmetic problem
while a crash is not. Every failure path falls back to the reference palette.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class Palette:
    """Colours used by the overlay. Defaults are the Omarchy reference."""

    background: str = "#1c1722"
    surface: str = "#252030"
    accent: str = "#3ddbd9"
    text: str = "#d0ccdd"
    text_dim: str = "#b8b4c8"
    muted: str = "#8b8798"
    warning: str = "#e8a33d"
    source: str = "default"


REFERENCE = Palette()


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _valid(colour: object) -> str | None:
    """Accept only things that look like hex colours."""
    if not isinstance(colour, str):
        return None
    text = colour.strip()
    if len(text) in (4, 7, 9) and text.startswith("#"):
        try:
            int(text[1:], 16)
        except ValueError:
            return None
        return text
    return None


def _first(data: dict, *keys: str) -> str | None:
    for key in keys:
        found = _valid(data.get(key))
        if found:
            return found
    return None


# --- Noctalia -------------------------------------------------------------


def noctalia_palette() -> Palette | None:
    """Read Noctalia's active colour scheme.

    Scheme files live at ``~/.config/noctalia/colorschemes/<Name>/<Name>.json``
    and carry Material-ish keys (primary, surface, on_surface, ...). Which
    scheme is active is recorded in settings.json under a key whose exact name
    has changed between Noctalia versions, so we probe a few and otherwise fall
    back to the only scheme present.
    """
    root = _config_home() / "noctalia"
    schemes = root / "colorschemes"
    if not schemes.is_dir():
        return None

    active: str | None = None
    settings = root / "settings.json"
    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        for key in ("colorScheme", "colorSchemeName", "scheme", "activeColorScheme"):
            value = data.get(key)
            if isinstance(value, str) and value:
                active = value
                break

    candidates: list[Path] = []
    if active:
        candidates.append(schemes / active / f"{active}.json")
    directories = sorted(p for p in schemes.iterdir() if p.is_dir())
    if len(directories) == 1:
        candidates.append(directories[0] / f"{directories[0].name}.json")
    for directory in directories:
        candidates.append(directory / f"{directory.name}.json")

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            colours = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(colours, dict):
            continue
        palette = _from_material(colours, source=f"noctalia:{candidate.parent.name}")
        if palette is not None:
            return palette
    return None


# --- DankMaterialShell ----------------------------------------------------

_DMS_CANDIDATES = (
    "DankMaterialShell/colors.json",
    "DankMaterialShell/settings.json",
    "dms/colors.json",
    "quickshell/dms/colors.json",
)


def dms_palette() -> Palette | None:
    """Read DankMaterialShell's Material 3 tokens.

    DMS generates a Material palette from the wallpaper. The file it lands in
    varies by version, so several known locations are tried; a state directory
    is checked too since generated colours are not strictly configuration.
    """
    roots = [_config_home()]
    state = os.environ.get("XDG_STATE_HOME")
    roots.append(Path(state) if state else Path.home() / ".local" / "state")

    for root in roots:
        for relative in _DMS_CANDIDATES:
            path = root / relative
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            # Some versions nest the palette under a "colors" key.
            colours = data.get("colors") if isinstance(data.get("colors"), dict) else data
            palette = _from_material(colours, source="dms")
            if palette is not None:
                return palette
    return None


def _from_material(colours: dict, source: str) -> Palette | None:
    """Map Material-style tokens onto our palette.

    Returns None when the file has no usable colours, so the caller can keep
    looking rather than adopting a half-empty palette.
    """
    background = _first(colours, "surface", "background", "mSurface", "surface_container")
    accent = _first(colours, "primary", "mPrimary", "accent", "tertiary")
    text = _first(colours, "on_surface", "onSurface", "mOnSurface", "foreground")
    dim = _first(colours, "on_surface_variant", "onSurfaceVariant", "mOnSurfaceVariant")
    muted = _first(colours, "outline", "mOutline", "on_surface_variant")
    warning = _first(colours, "error", "mError", "tertiary")

    if not any((background, accent, text)):
        return None

    palette = REFERENCE
    if background:
        palette = replace(palette, background=background)
    if accent:
        palette = replace(palette, accent=accent)
    if text:
        palette = replace(palette, text=text, text_dim=dim or text)
    if dim:
        palette = replace(palette, text_dim=dim)
    if muted:
        palette = replace(palette, muted=muted)
    if warning:
        palette = replace(palette, warning=warning)
    return replace(palette, source=source)


# --- entry point ----------------------------------------------------------


def current_palette(backend_name: str | None = None) -> Palette:
    """Best palette for the active session, falling back to the reference."""
    if backend_name == "niri":
        order = (noctalia_palette, dms_palette)
    elif backend_name == "mango":
        order = (dms_palette, noctalia_palette)
    elif backend_name == "cosmic":
        # Neither shell runs on COSMIC; the reference palette is correct there.
        return REFERENCE
    else:
        order = (noctalia_palette, dms_palette)

    for reader in order:
        try:
            palette = reader()
        except Exception:  # noqa: BLE001 - theming must never break the overlay
            palette = None
        if palette is not None:
            return palette
    return REFERENCE
