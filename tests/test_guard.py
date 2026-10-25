import pytest

from cachy_shortcuts import editor, guard
from cachy_shortcuts.model import Chord


@pytest.mark.parametrize(
    "text, level",
    [
        ("Super+Slash", 0),
        ("Ctrl+Down", 0),
        ("Alt+Return", 0),
        ("XF86AudioMute", 0),
        ("Shift+XF86AudioMute", 0),
        ("Print", 0),
        ("F11", 1),
        ("Down", 2),
        ("Shift+Down", 2),
        ("Return", 2),
        ("Space", 2),
        ("Escape", 2),
        ("a", 2),
        ("Shift+a", 2),
        ("5", 2),
        ("slash", 2),
    ],
)
def test_risk_levels(text, level):
    assert guard.risk(Chord.parse(text)) == level


def test_editor_refuses_unconfirmed_bare_chord():
    chord = Chord.parse("Down")
    with pytest.raises(editor.RiskRefused) as exc:
        editor._check_risk(chord, 0)
    assert exc.value.needed == 2
    with pytest.raises(editor.RiskRefused):
        editor._check_risk(chord, 1)
    editor._check_risk(chord, 2)


def test_unchanged_chord_is_not_rechecked():
    chord = Chord.parse("Down")
    editor._check_risk(chord, 0, old=chord)
