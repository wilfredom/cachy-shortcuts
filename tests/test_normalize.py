"""The cross-dialect equivalence tests.

If these pass, a chord written in any of the three config formats compares
equal to the same chord written in the other two -- which is the property
conflict detection and search both depend on.
"""

import pytest

from cachy_shortcuts.model import Category, Chord, infer_category
from cachy_shortcuts.normalize import display_key, normalize_key, normalize_mod


class TestCrossDialectEquivalence:
    def test_same_chord_from_all_three_backends(self):
        niri = Chord.parse("Mod+Shift+Slash")
        cosmic = Chord.from_parts(["Super", "Shift"], "slash")
        mango = Chord.from_parts("SUPER+SHIFT".split("+"), "slash")
        assert niri == cosmic == mango
        assert niri.canonical == "super+shift+slash"

    def test_modifier_order_is_irrelevant(self):
        assert Chord.parse("Shift+Mod+Ctrl+B") == Chord.parse("Ctrl+Super+Shift+B")

    def test_symbol_and_name_key_forms_agree(self):
        assert Chord.parse("Super+Slash") == Chord.parse("Super+/")
        assert Chord.parse("Super+Minus") == Chord.parse("Super+-")
        assert Chord.parse("Super+BracketLeft") == Chord.parse("Super+[")

    def test_return_aliases(self):
        assert Chord.parse("Mod+Return") == Chord.parse("SUPER+enter")

    def test_case_insensitivity(self):
        assert Chord.parse("MOD+SHIFT+B") == Chord.parse("mod+shift+b")


class TestModifiers:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Mod", "super"), ("SUPER", "super"), ("win", "super"), ("Logo", "super"),
            ("Ctrl", "ctrl"), ("CONTROL", "ctrl"), ("Alt", "alt"), ("mod1", "alt"),
            ("Shift", "shift"),
        ],
    )
    def test_aliases(self, raw, expected):
        assert normalize_mod(raw) == expected

    def test_none_is_dropped(self):
        assert normalize_mod("NONE") is None
        assert Chord.from_parts(["NONE"], "XF86MonBrightnessUp").mods == ()

    def test_unknown_modifier_raises(self):
        # Silently dropping an unknown modifier would make two distinct chords
        # compare equal, so this must be loud.
        with pytest.raises(KeyError):
            normalize_mod("Hyper")

    def test_mango_raw_keycode_modifier_is_preserved(self):
        assert normalize_mod("code:64") == "code:64"


class TestKeys:
    def test_mango_raw_keycode_stays_opaque(self):
        assert normalize_key("code:24") == "code:24"

    def test_xf86_keys_pass_through_lowercased(self):
        assert normalize_key("XF86AudioRaiseVolume") == "xf86audioraisevolume"

    def test_empty_key_raises(self):
        with pytest.raises(ValueError):
            normalize_key("   ")


class TestDisplay:
    def test_omarchy_convention(self):
        # Modifiers space-joined, "+" only before the final key.
        assert Chord.parse("Super+Shift+B").display() == "SUPER SHIFT + B"
        assert Chord.parse("Super+Return").display() == "SUPER + RETURN"

    def test_bare_key_has_no_plus(self):
        assert Chord.parse("Print").display() == "PRINT"

    def test_punctuation_renders_as_symbol(self):
        assert Chord.parse("Super+Slash").display() == "SUPER + /"

    def test_media_keys_get_friendly_names(self):
        assert display_key("xf86audioraisevolume") == "VOL UP"
        assert display_key("xf86monbrightnessdown") == "BRIGHT DOWN"

    def test_unknown_media_key_degrades_readably(self):
        assert display_key("xf86launcha") == "LAUNCHA"


class TestCategoryInference:
    @pytest.mark.parametrize(
        "action,expected",
        [
            ('spawn "alacritty"', Category.LAUNCH),
            ("killclient,", Category.WINDOWS),
            ("togglefloating,", Category.WINDOWS),
            ("view,1", Category.WORKSPACES),
            ("spawn,brightnessctl set +5%", Category.MEDIA),
            ("XF86AudioRaiseVolume", Category.MEDIA),
            ('spawn-sh "grim -g \\"$(slurp)\\""', Category.SCREENSHOT),
            ("quit", Category.SYSTEM),
            ("spawn,loginctl lock-session", Category.SYSTEM),
        ],
    )
    def test_rules(self, action, expected):
        assert infer_category(action) == expected

    def test_media_beats_launch_when_both_match(self):
        # "spawn" would match LAUNCH, but a volume command is Media first.
        assert infer_category("spawn,pamixer -i 5") == Category.MEDIA

    def test_screenshot_beats_launch(self):
        assert infer_category('spawn "grim"') == Category.SCREENSHOT

    def test_unmatched_falls_back_to_other(self):
        assert infer_category("frobnicate") == Category.OTHER
