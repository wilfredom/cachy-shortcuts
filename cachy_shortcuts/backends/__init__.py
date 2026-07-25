"""Compositor backends."""

from .base import Backend, FloatRule, ParsedFile, escape_regex
from .cosmic import CosmicBackend
from .hyprland import HyprlandBackend
from .mango import MangoBackend
from .niri import NiriBackend

ALL_BACKENDS: tuple[type[Backend], ...] = (
    NiriBackend,
    HyprlandBackend,
    CosmicBackend,
    MangoBackend,
)

__all__ = [
    "ALL_BACKENDS",
    "Backend",
    "CosmicBackend",
    "FloatRule",
    "HyprlandBackend",
    "MangoBackend",
    "NiriBackend",
    "ParsedFile",
    "escape_regex",
]
