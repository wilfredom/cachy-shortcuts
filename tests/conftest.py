from pathlib import Path

import pytest

from cachy_shortcuts.backends import (
    CosmicBackend,
    HyprlandBackend,
    MangoBackend,
    NiriBackend,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def niri():
    return NiriBackend(config_root=FIXTURES / "niri")


@pytest.fixture
def hyprland():
    """A Hyprland config as it looks under the Noctalia shell."""
    return HyprlandBackend(config_root=FIXTURES / "hyprland")


@pytest.fixture
def hyprland_vanilla():
    """The same compositor with no shell: no variables, no Noctalia binds."""
    return HyprlandBackend(config_root=FIXTURES / "hyprland-vanilla")


@pytest.fixture
def mango():
    return MangoBackend(config_root=FIXTURES / "mango")


@pytest.fixture
def cosmic():
    return CosmicBackend(
        config_root=FIXTURES / "cosmic" / "config",
        system_root=FIXTURES / "cosmic" / "system",
    )


@pytest.fixture
def all_backends(niri, hyprland, mango, cosmic):
    return [niri, hyprland, mango, cosmic]


def by_chord(shortcuts):
    return {s.chord.canonical: s for s in shortcuts}
