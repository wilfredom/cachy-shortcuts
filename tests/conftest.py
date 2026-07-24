from pathlib import Path

import pytest

from cachy_shortcuts.backends import CosmicBackend, MangoBackend, NiriBackend

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def niri():
    return NiriBackend(config_root=FIXTURES / "niri")


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
def all_backends(niri, mango, cosmic):
    return [niri, mango, cosmic]


def by_chord(shortcuts):
    return {s.chord.canonical: s for s in shortcuts}
