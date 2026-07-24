"""The overlay's pure state machine -- filtering, grouping, selection, edits.

Tested without GTK so the interaction logic (capture-in-place, live conflict
feedback, gap surfacing) is verified the same way the rest of the core is.
"""

from pathlib import Path

import pytest

from cachy_shortcuts import usage
from cachy_shortcuts.model import Category, Chord, Shortcut, SourceRef
from cachy_shortcuts.ui.viewmodel import Mode, OverlayModel, RowKind


def make(chord_text, action, category=Category.LAUNCH, description=""):
    return Shortcut(
        chord=Chord.parse(chord_text),
        action=action,
        description=description,
        category=category,
        source=SourceRef("niri", Path("/tmp/x.kdl"), 0, 1, 1),
    )


@pytest.fixture
def isolated_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


@pytest.fixture
def sample():
    return [
        make("Super+Return", "spawn alacritty", Category.LAUNCH, "Terminal"),
        make("Super+B", "spawn firefox", Category.LAUNCH, "Browser"),
        make("Super+Q", "close-window", Category.WINDOWS),
        make("Super+1", "focus-workspace 1", Category.WORKSPACES),
    ]


class TestRowBuilding:
    def test_rows_are_grouped_by_category_with_headers(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        sections = [r.title for r in model.rows if r.kind is RowKind.SECTION]
        assert sections == ["Launch", "Windows", "Workspaces"]

    def test_shortcuts_within_a_section_are_sorted_by_display(self, isolated_usage):
        model = OverlayModel(
            shortcuts=[
                make("Super+Z", "spawn z", Category.LAUNCH),
                make("Super+A", "spawn a", Category.LAUNCH),
            ]
        )
        launch_rows = [
            r for r in model.rows if r.kind is RowKind.SHORTCUT
        ]
        assert [r.shortcut.chord.display() for r in launch_rows] == [
            "SUPER + A",
            "SUPER + Z",
        ]

    def test_empty_categories_produce_no_header(self, isolated_usage):
        model = OverlayModel(shortcuts=[make("Super+B", "spawn firefox", Category.LAUNCH)])
        sections = [r.title for r in model.rows if r.kind is RowKind.SECTION]
        assert sections == ["Launch"]

    def test_set_shortcuts_rebuilds_rows(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=[])
        assert model.rows == []
        model.set_shortcuts(sample)
        assert len(model.rows) > 0


class TestSearch:
    def test_empty_query_shows_everything(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        assert model.count() == len(sample)

    def test_query_filters_by_label(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.set_query("firefox")
        assert model.count() == 1
        assert model.current().label == "Browser"

    def test_query_filters_by_chord_display(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.set_query("super + q")
        assert model.count() == 1

    def test_multi_term_query_narrows(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.set_query("super b")
        assert model.count() == 1
        model.set_query("super nonexistent")
        assert model.count() == 0

    def test_query_is_case_insensitive(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.set_query("FIREFOX")
        assert model.count() == 1

    def test_clearing_the_query_restores_all_sections(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.set_query("firefox")
        model.set_query("")
        assert model.count() == len(sample)


class TestSelection:
    def test_selection_starts_on_a_selectable_row(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        assert model.current() is not None

    def test_move_down_advances_through_selectable_rows(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        first = model.current()
        model.move(1)
        assert model.current() is not first

    def test_move_skips_section_headers(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        seen_kinds = set()
        for _ in range(model.count()):
            row = model.rows[model.selected]
            seen_kinds.add(row.kind)
            model.move(1)
        assert seen_kinds == {RowKind.SHORTCUT}

    def test_move_clamps_at_the_end_instead_of_wrapping(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        for _ in range(len(sample) + 5):
            model.move(1)
        last = model.current()
        model.move(1)
        assert model.current() is last

    def test_move_clamps_at_the_start(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.move(-5)
        assert model.current() is not None

    def test_move_on_empty_model_does_not_crash(self, isolated_usage):
        model = OverlayModel(shortcuts=[])
        model.move(1)
        assert model.current() is None

    def test_selection_survives_a_query_that_still_matches(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.move(1)  # land on Browser
        model.set_query("firefox")
        assert model.current().label == "Browser"


class TestEditStateMachine:
    def test_begin_capture_requires_a_selection(self, isolated_usage):
        model = OverlayModel(shortcuts=[])
        assert model.begin_capture() is False
        assert model.mode is Mode.BROWSE

    def test_begin_capture_switches_mode(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        assert model.begin_capture() is True
        assert model.mode is Mode.CAPTURE_CHORD

    def test_capture_reports_no_conflict_for_a_free_chord(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.begin_capture()
        claim = model.capture(Chord.parse("Super+Z"))
        assert claim is None
        assert "free" in model.status

    def test_capture_reports_conflict_for_a_taken_chord(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.set_query("terminal")  # select something other than Super+B
        model.begin_capture()
        claim = model.capture(Chord.parse("Super+B"))
        assert claim is not None
        assert "SUPER + B" in model.status

    def test_capturing_the_current_shortcuts_own_chord_is_not_a_self_conflict(
        self, sample, isolated_usage
    ):
        model = OverlayModel(shortcuts=sample)
        model.set_query("firefox")  # select Super+B itself
        model.begin_capture()
        claim = model.capture(Chord.parse("Super+B"))
        assert claim is None

    def test_cancel_returns_to_browse(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.begin_capture()
        model.capture(Chord.parse("Super+Z"))
        model.cancel()
        assert model.mode is Mode.BROWSE
        assert model.pending_chord is None
        assert model.status == ""

    def test_begin_command_edit_seeds_the_draft(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.begin_command_edit()
        assert model.mode is Mode.EDIT_COMMAND
        assert model.draft_command == model.current().action

    def test_begin_delete_asks_for_confirmation(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.begin_delete()
        assert model.mode is Mode.CONFIRM_DELETE

    def test_begin_add_is_independent_of_the_current_selection(
        self, sample, isolated_usage
    ):
        model = OverlayModel(shortcuts=sample)
        model.set_query("firefox")  # select Super+B
        assert model.begin_add() is True
        assert model.editing_target is None
        # Capturing Super+B (the selected row's own chord) must still report
        # a conflict, because we are adding a *new* binding, not rebinding
        # the selected one.
        claim = model.capture(Chord.parse("Super+B"))
        assert claim is not None

    def test_begin_add_works_with_nothing_selected(self, isolated_usage):
        model = OverlayModel(shortcuts=[])
        assert model.begin_add() is True
        assert model.mode is Mode.CAPTURE_CHORD

    def test_begin_capture_excludes_only_the_target_being_rebound(
        self, sample, isolated_usage
    ):
        model = OverlayModel(shortcuts=sample)
        model.set_query("firefox")  # select Super+B
        model.begin_capture()
        assert model.editing_target.label == "Browser"
        # Recapturing the same chord the target already owns is not a
        # self-conflict.
        assert model.capture(Chord.parse("Super+B")) is None
        # But claiming a *different* existing chord still conflicts.
        assert model.capture(Chord.parse("Super+Q")) is not None

    def test_cancel_clears_editing_target(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.begin_capture()
        model.cancel()
        assert model.editing_target is None


class TestHintsAndHeader:
    def test_hint_changes_per_mode(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        browse_hint = model.hint()
        model.begin_capture()
        assert model.hint() != browse_hint

    def test_header_shows_app_context_when_set(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        assert model.header() == "Keybindings"
        model.app_context = "firefox"
        assert "firefox" in model.header()


class TestLearningModeIntegration:
    def test_repeated_lookups_surface_as_a_gaps_section(self, sample, isolated_usage):
        for _ in range(3):
            usage.record_lookup("super+b")
        model = OverlayModel(shortcuts=sample)
        assert model.rows[0].title == "You keep looking these up"
        assert model.rows[1].shortcut.chord.canonical == "super+b"

    def test_gap_section_is_hidden_while_searching(self, sample, isolated_usage):
        for _ in range(3):
            usage.record_lookup("super+b")
        model = OverlayModel(shortcuts=sample)
        model.set_query("terminal")
        assert all(r.title != "You keep looking these up" for r in model.rows)

    def test_no_gap_section_below_the_threshold(self, sample, isolated_usage):
        usage.record_lookup("super+b")  # only once
        model = OverlayModel(shortcuts=sample)
        assert model.rows[0].title != "You keep looking these up"

    def test_searching_and_landing_on_a_result_records_a_lookup(
        self, sample, isolated_usage
    ):
        model = OverlayModel(shortcuts=sample)
        model.set_query("firefox")
        assert usage.counts().get("super+b") == 1

    def test_a_broken_usage_store_does_not_crash_row_building(
        self, sample, isolated_usage, monkeypatch
    ):
        def explode():
            raise RuntimeError("disk error")

        monkeypatch.setattr(usage, "counts", explode)
        model = OverlayModel(shortcuts=sample)  # must not raise
        assert model.count() == len(sample)
