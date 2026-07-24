"""App-specific cheat sheets: bundled packs, user overrides, and the
read-only guard that keeps them from being "edited" into a write that goes
nowhere.
"""

from pathlib import Path

import pytest

from cachy_shortcuts import cheatsheets
from cachy_shortcuts.model import Category, Chord, Shortcut, SourceRef
from cachy_shortcuts.ui.viewmodel import Mode, OverlayModel


class TestBundledPacks:
    def test_firefox_pack_matches_by_substring(self):
        entries = cheatsheets.load_for("firefox")
        assert entries
        assert all(s.category is Category.APP for s in entries)

    def test_entries_are_read_only(self):
        entries = cheatsheets.load_for("firefox")
        assert all(s.source is None for s in entries)
        assert all(s.extras.get("readonly") for s in entries)

    def test_match_is_case_insensitive_and_substring(self):
        assert cheatsheets.load_for("Firefox") == cheatsheets.load_for("firefox")
        assert cheatsheets.load_for("org.mozilla.firefox")

    def test_vscode_pack_matches_several_variants(self):
        for app_id in ("code", "code-oss", "code-insiders", "VSCodium"):
            assert cheatsheets.load_for(app_id), app_id

    def test_unknown_app_returns_nothing(self):
        assert cheatsheets.load_for("some-random-app-nobody-wrote-a-pack-for") == []

    def test_none_or_empty_app_id_returns_nothing(self):
        assert cheatsheets.load_for(None) == []
        assert cheatsheets.load_for("") == []

    def test_chords_are_parseable_and_displayable(self):
        for pack in cheatsheets.available_packs():
            for chord_text, _ in pack.entries:
                Chord.parse(chord_text)  # must not raise

    def test_available_packs_lists_all_bundled(self):
        names = {p.name for p in cheatsheets.available_packs()}
        assert {"Alacritty", "Firefox", "VS Code", "Files"} <= names


class TestUserOverrides:
    @pytest.fixture
    def user_packs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        d = tmp_path / "cachy-shortcuts" / "cheatsheets"
        d.mkdir(parents=True)
        return d

    def test_user_pack_for_a_new_app_is_found(self, user_packs):
        (user_packs / "obsidian.yaml").write_text(
            "name: Obsidian\n"
            "match:\n"
            "  - obsidian\n"
            "shortcuts:\n"
            "  - chord: \"Ctrl+O\"\n"
            "    description: \"Quick switcher\"\n"
        )
        entries = cheatsheets.load_for("obsidian")
        assert len(entries) == 1
        assert entries[0].label == "Quick switcher"

    def test_user_pack_overrides_a_bundled_one_with_the_same_filename(self, user_packs):
        (user_packs / "firefox.yaml").write_text(
            "name: Firefox (mine)\n"
            "match:\n"
            "  - firefox\n"
            "shortcuts:\n"
            "  - chord: \"Ctrl+Shift+X\"\n"
            "    description: \"My custom thing\"\n"
        )
        entries = cheatsheets.load_for("firefox")
        assert len(entries) == 1
        assert entries[0].label == "My custom thing"

    def test_malformed_user_pack_is_skipped_not_fatal(self, user_packs):
        (user_packs / "broken.yaml").write_text("not: [valid, yaml, {{{")
        # Must not raise, and other packs still load.
        assert cheatsheets.load_for("firefox")

    def test_pack_with_no_shortcuts_is_skipped(self, user_packs):
        (user_packs / "empty.yaml").write_text("name: Empty\nmatch:\n  - emptyapp\n")
        assert cheatsheets.load_for("emptyapp") == []


class TestMinimalYamlFallback:
    """The dependency-free parser used when PyYAML is not installed."""

    def test_parses_the_pack_schema(self):
        text = (
            "name: Example\n"
            "match:\n"
            "  - foo\n"
            "  - bar\n"
            "shortcuts:\n"
            "  - chord: \"Ctrl+A\"\n"
            "    description: \"Select all\"\n"
            "  - chord: \"Ctrl+B\"\n"
            "    description: \"Bold\"\n"
        )
        data = cheatsheets._minimal_yaml_load(text)
        assert data["name"] == "Example"
        assert data["match"] == ["foo", "bar"]
        assert data["shortcuts"] == [
            {"chord": "Ctrl+A", "description": "Select all"},
            {"chord": "Ctrl+B", "description": "Bold"},
        ]

    def test_ignores_comments_and_blank_lines(self):
        text = "# a comment\nname: X\n\nmatch:\n  - x\n"
        data = cheatsheets._minimal_yaml_load(text)
        assert data["name"] == "X"

    def test_strips_quotes(self):
        data = cheatsheets._minimal_yaml_load('name: "Quoted Name"\n')
        assert data["name"] == "Quoted Name"


class TestReadOnlyGuard:
    def _app_shortcut(self):
        return Shortcut(
            chord=Chord.parse("Ctrl+T"),
            action="New tab",
            description="New tab",
            category=Category.APP,
            source=None,
            extras={"readonly": True},
        )

    def _editable_shortcut(self):
        return Shortcut(
            chord=Chord.parse("Super+B"),
            action="spawn firefox",
            category=Category.LAUNCH,
            source=SourceRef("niri", Path("/tmp/x"), 0, 1, 1),
        )

    def test_begin_capture_refuses_a_cheatsheet_entry(self):
        model = OverlayModel(shortcuts=[self._app_shortcut()])
        assert model.begin_capture() is False
        assert model.mode is Mode.BROWSE
        assert "app's own settings" in model.status

    def test_begin_command_edit_refuses_a_cheatsheet_entry(self):
        model = OverlayModel(shortcuts=[self._app_shortcut()])
        assert model.begin_command_edit() is False
        assert model.mode is Mode.BROWSE

    def test_begin_delete_refuses_a_cheatsheet_entry(self):
        model = OverlayModel(shortcuts=[self._app_shortcut()])
        assert model.begin_delete() is False
        assert model.mode is Mode.BROWSE

    def test_editable_shortcuts_are_unaffected(self):
        model = OverlayModel(shortcuts=[self._editable_shortcut()])
        assert model.begin_capture() is True
        assert model.mode is Mode.CAPTURE_CHORD

    def test_begin_add_is_never_blocked_by_the_guard(self):
        # Adding a new binding has no target yet, so the read-only check
        # (which only applies to an existing selection) must not apply.
        model = OverlayModel(shortcuts=[self._app_shortcut()])
        assert model.begin_add() is True
