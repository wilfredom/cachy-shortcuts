"""Palette discovery for Noctalia and DankMaterialShell.

Every failure path here must degrade to the reference palette rather than
raise, since a themed overlay is a nicety and a crashed overlay is not.
"""

import json

import pytest

from cachy_shortcuts import theming


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    home = tmp_path / "config"
    home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    return home


class TestNoctaliaPalette:
    def test_no_noctalia_dir_returns_none(self, config_home):
        assert theming.noctalia_palette() is None

    def test_single_scheme_is_used_without_settings(self, config_home):
        scheme_dir = config_home / "noctalia" / "colorschemes" / "Sunset"
        scheme_dir.mkdir(parents=True)
        (scheme_dir / "Sunset.json").write_text(
            json.dumps({"primary": "#ff00ff", "surface": "#111111", "on_surface": "#eeeeee"})
        )
        palette = theming.noctalia_palette()
        assert palette is not None
        assert palette.accent == "#ff00ff"
        assert palette.background == "#111111"
        assert palette.source == "noctalia:Sunset"

    def test_active_scheme_is_selected_from_settings(self, config_home):
        root = config_home / "noctalia"
        for name, colour in (("Sunset", "#ff0000"), ("Midnight", "#0000ff")):
            d = root / "colorschemes" / name
            d.mkdir(parents=True)
            (d / f"{name}.json").write_text(json.dumps({"primary": colour}))
        (root / "settings.json").write_text(json.dumps({"colorScheme": "Midnight"}))
        palette = theming.noctalia_palette()
        assert palette.source == "noctalia:Midnight"
        assert palette.accent == "#0000ff"

    def test_falls_through_to_other_schemes_when_active_has_no_usable_colours(
        self, config_home
    ):
        root = config_home / "noctalia"
        unusable = root / "colorschemes" / "Blank"
        unusable.mkdir(parents=True)
        (unusable / "Blank.json").write_text(json.dumps({"unrelated": "nope"}))
        usable = root / "colorschemes" / "Fallback"
        usable.mkdir(parents=True)
        (usable / "Fallback.json").write_text(json.dumps({"primary": "#00ff00"}))
        (root / "settings.json").write_text(json.dumps({"colorScheme": "Blank"}))

        palette = theming.noctalia_palette()
        assert palette is not None
        assert palette.accent == "#00ff00"

    def test_malformed_scheme_json_is_skipped_not_fatal(self, config_home):
        scheme_dir = config_home / "noctalia" / "colorschemes" / "Broken"
        scheme_dir.mkdir(parents=True)
        (scheme_dir / "Broken.json").write_text("{not json")
        assert theming.noctalia_palette() is None

    def test_scheme_with_no_usable_colours_is_skipped(self, config_home):
        scheme_dir = config_home / "noctalia" / "colorschemes" / "Empty"
        scheme_dir.mkdir(parents=True)
        (scheme_dir / "Empty.json").write_text(json.dumps({"unrelated": "value"}))
        assert theming.noctalia_palette() is None

    def test_invalid_hex_colour_is_rejected(self, config_home):
        scheme_dir = config_home / "noctalia" / "colorschemes" / "Bad"
        scheme_dir.mkdir(parents=True)
        (scheme_dir / "Bad.json").write_text(json.dumps({"primary": "not-a-colour"}))
        assert theming.noctalia_palette() is None


class TestDMSPalette:
    def test_no_dms_files_returns_none(self, config_home):
        assert theming.dms_palette() is None

    def test_reads_material_tokens(self, config_home):
        d = config_home / "DankMaterialShell"
        d.mkdir()
        (d / "colors.json").write_text(
            json.dumps({"mPrimary": "#00ffaa", "mSurface": "#0a0a0a", "mOnSurface": "#fafafa"})
        )
        palette = theming.dms_palette()
        assert palette is not None
        assert palette.accent == "#00ffaa"
        assert palette.source == "dms"

    def test_nested_colors_key_is_unwrapped(self, config_home):
        d = config_home / "DankMaterialShell"
        d.mkdir()
        (d / "settings.json").write_text(
            json.dumps({"colors": {"primary": "#123456", "surface": "#654321"}})
        )
        palette = theming.dms_palette()
        assert palette is not None
        assert palette.accent == "#123456"

    def test_state_home_is_also_searched(self, config_home, tmp_path, monkeypatch):
        state = tmp_path / "state"
        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        d = state / "DankMaterialShell"
        d.mkdir(parents=True)
        (d / "colors.json").write_text(json.dumps({"primary": "#abcdef"}))
        palette = theming.dms_palette()
        assert palette is not None
        assert palette.accent == "#abcdef"


class TestCurrentPalette:
    def test_falls_back_to_reference_with_nothing_on_disk(self, config_home):
        assert theming.current_palette("niri") == theming.REFERENCE
        assert theming.current_palette("mango") == theming.REFERENCE
        assert theming.current_palette(None) == theming.REFERENCE

    def test_cosmic_always_uses_reference(self, config_home):
        # Even if a noctalia scheme happens to exist, COSMIC runs neither shell.
        scheme_dir = config_home / "noctalia" / "colorschemes" / "X"
        scheme_dir.mkdir(parents=True)
        (scheme_dir / "X.json").write_text(json.dumps({"primary": "#ff0000"}))
        assert theming.current_palette("cosmic") == theming.REFERENCE

    def test_niri_prefers_noctalia_over_dms(self, config_home):
        scheme_dir = config_home / "noctalia" / "colorschemes" / "X"
        scheme_dir.mkdir(parents=True)
        (scheme_dir / "X.json").write_text(json.dumps({"primary": "#ff0000"}))
        dms_dir = config_home / "DankMaterialShell"
        dms_dir.mkdir()
        (dms_dir / "colors.json").write_text(json.dumps({"primary": "#00ff00"}))
        assert theming.current_palette("niri").accent == "#ff0000"

    def test_mango_prefers_dms_over_noctalia(self, config_home):
        scheme_dir = config_home / "noctalia" / "colorschemes" / "X"
        scheme_dir.mkdir(parents=True)
        (scheme_dir / "X.json").write_text(json.dumps({"primary": "#ff0000"}))
        dms_dir = config_home / "DankMaterialShell"
        dms_dir.mkdir()
        (dms_dir / "colors.json").write_text(json.dumps({"primary": "#00ff00"}))
        assert theming.current_palette("mango").accent == "#00ff00"

    def test_a_reader_exception_does_not_propagate(self, config_home, monkeypatch):
        def explode():
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(theming, "noctalia_palette", explode)
        assert theming.current_palette("niri") == theming.REFERENCE
