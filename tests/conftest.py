from pathlib import Path

import pytest

from cachy_shortcuts.backends import (
    CosmicBackend,
    HyprlandBackend,
    MangoBackend,
    NiriBackend,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_live_reload(monkeypatch):
    """A write test must not ask the developer's running compositor to reload.

    Tests run from inside a real session as often as not, and every
    successful edit ends in ``backend.reload()``.
    """
    from cachy_shortcuts.backends.base import Backend

    monkeypatch.setattr(Backend, "reload", lambda self: None)
    for cls in (CosmicBackend, HyprlandBackend, MangoBackend, NiriBackend):
        if "reload" in vars(cls):
            monkeypatch.setattr(cls, "reload", lambda self: None)


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
def hyprland_lua():
    """Hyprland 0.55+ running hyprland.lua, with a stale hyprland.conf beside it."""
    return HyprlandBackend(config_root=FIXTURES / "hyprland-lua")


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
