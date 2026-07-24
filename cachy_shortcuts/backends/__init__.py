"""Compositor backends."""

from .base import Backend, ParsedFile
from .cosmic import CosmicBackend
from .mango import MangoBackend
from .niri import NiriBackend

ALL_BACKENDS: tuple[type[Backend], ...] = (NiriBackend, CosmicBackend, MangoBackend)

__all__ = [
    "ALL_BACKENDS",
    "Backend",
    "CosmicBackend",
    "MangoBackend",
    "NiriBackend",
    "ParsedFile",
]
