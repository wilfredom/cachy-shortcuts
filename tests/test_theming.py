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


class TestContrast:
    """The floor that stops a shell's palette from making the overlay unreadable."""

    def test_known_ratios(self):
        assert theming.contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0)
        assert theming.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
        assert theming.contrast_ratio("#777777", "#777777") == pytest.approx(1.0)

    def test_ratio_is_symmetric(self):
        a = theming.contrast_ratio("#3ddbd9", "#1c1722")
        b = theming.contrast_ratio("#1c1722", "#3ddbd9")
        assert a == pytest.approx(b)

    def test_short_and_alpha_hex_are_understood(self):
        assert theming.contrast_ratio("#fff", "#000") == pytest.approx(21.0)
        assert theming.contrast_ratio("#ffffffcc", "#000000") == pytest.approx(21.0)

    def test_unparseable_colour_is_treated_as_no_contrast(self):
        # 1.0 is the "don't touch this" answer: fit_contrast leaves it alone
        # rather than inventing a colour from something it didn't understand.
        assert theming.contrast_ratio("not-a-colour", "#000000") == 1.0
        assert theming.fit_contrast("not-a-colour", "#000000", 7.0) == "not-a-colour"

    def test_the_reference_palette_already_passes(self):
        assert theming.ensure_contrast(theming.REFERENCE) == theming.REFERENCE

    def test_dark_background_lightens_the_foreground(self):
        fixed = theming.fit_contrast("#2a2a30", "#1c1722", 7.0)
        assert theming.contrast_ratio(fixed, "#1c1722") >= 7.0

    def test_light_background_darkens_the_foreground(self):
        fixed = theming.fit_contrast("#eeeef4", "#fbfbfd", 7.0)
        assert theming.contrast_ratio(fixed, "#fbfbfd") >= 7.0

    def test_a_passing_colour_is_returned_untouched(self):
        assert theming.fit_contrast("#ffffff", "#000000", 7.0) == "#ffffff"

    def test_hue_is_broadly_preserved(self):
        """Nudging lightness, not repainting: a dark red stays red."""
        fixed = theming.fit_contrast("#330000", "#1c1722", 7.0)
        r, g, b = (int(fixed[i : i + 2], 16) for i in (1, 3, 5))
        assert r > g and r > b

    def test_every_token_clears_its_floor(self):
        hostile = theming.Palette(
            background="#1a1a20",
            text="#3a3a44",
            text_dim="#2e2e38",
            accent="#242430",
            muted="#202028",
            warning="#33262a",
        )
        fixed = theming.ensure_contrast(hostile)
        for name, minimum in theming._MINIMUM_CONTRAST.items():
            ratio = theming.contrast_ratio(getattr(fixed, name), fixed.background)
            assert ratio >= minimum, f"{name}: {ratio:.2f} < {minimum}"

    def test_background_and_source_are_left_alone(self):
        hostile = theming.Palette(background="#1a1a20", text="#1b1b21", source="dms")
        fixed = theming.ensure_contrast(hostile)
        assert fixed.background == "#1a1a20"
        assert fixed.source == "dms"

    def test_a_mid_grey_background_still_returns_something_usable(self):
        """Nothing clears 7:1 on mid-grey; the answer must still be a colour."""
        fixed = theming.fit_contrast("#808080", "#808080", 7.0)
        assert fixed.startswith("#")
        assert len(fixed) == 7


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

    def _both_shells(self, config_home):
        """Two schemes whose accents already clear the contrast floor.

        Colours that need nudging would make these tests about
        ``ensure_contrast`` rather than about which shell wins.
        """
        scheme_dir = config_home / "noctalia" / "colorschemes" / "X"
        scheme_dir.mkdir(parents=True)
        (scheme_dir / "X.json").write_text(json.dumps({"primary": "#ff5555"}))
        dms_dir = config_home / "DankMaterialShell"
        dms_dir.mkdir()
        (dms_dir / "colors.json").write_text(json.dumps({"primary": "#55ff55"}))

    def test_niri_prefers_noctalia_over_dms(self, config_home):
        self._both_shells(config_home)
        palette = theming.current_palette("niri")
        assert palette.accent == "#ff5555"
        assert palette.source.startswith("noctalia")

    def test_mango_prefers_dms_over_noctalia(self, config_home):
        self._both_shells(config_home)
        palette = theming.current_palette("mango")
        assert palette.accent == "#55ff55"
        assert palette.source == "dms"

    def test_a_shell_palette_is_raised_to_the_contrast_floor(self, config_home):
        """A theme too dark to read must come back readable, not verbatim."""
        scheme_dir = config_home / "noctalia" / "colorschemes" / "X"
        scheme_dir.mkdir(parents=True)
        (scheme_dir / "X.json").write_text(
            json.dumps({"surface": "#1a1a20", "on_surface": "#232329", "primary": "#242430"})
        )
        palette = theming.current_palette("niri")
        assert palette.text != "#232329"
        assert theming.contrast_ratio(palette.text, palette.background) >= 7.0
        assert theming.contrast_ratio(palette.accent, palette.background) >= 4.5

    def test_a_reader_exception_does_not_propagate(self, config_home, monkeypatch):
        def explode():
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(theming, "noctalia_palette", explode)
        assert theming.current_palette("niri") == theming.REFERENCE
