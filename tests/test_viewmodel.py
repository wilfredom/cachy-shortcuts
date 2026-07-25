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


class TestPagedNavigation:
    def test_page_down_moves_further_than_one_row(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.move_page(1, page=2)
        assert model.selectable_indexes.index(model.selected) == 2

    def test_paging_clamps_at_the_ends(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.move_page(1, page=99)
        assert model.selected == model.selectable_indexes[-1]
        model.move_page(-1, page=99)
        assert model.selected == model.selectable_indexes[0]

    def test_end_and_home_jump_to_the_edges(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.move_to_edge(1)
        assert model.selected == model.selectable_indexes[-1]
        model.move_to_edge(-1)
        assert model.selected == model.selectable_indexes[0]

    def test_edges_on_an_empty_model_do_not_crash(self, isolated_usage):
        model = OverlayModel(shortcuts=[])
        model.move_to_edge(1)
        model.move_page(1)
        assert model.current() is None


class TestCreateRow:
    def test_a_search_with_no_matches_offers_to_create(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.set_query("obsidian")
        assert model.rows[-1].kind is RowKind.CREATE
        assert model.rows[-1].title == "obsidian"

    def test_no_create_row_when_something_matched(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.set_query("firefox")
        assert all(r.kind is not RowKind.CREATE for r in model.rows)

    def test_no_create_row_without_a_query(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        assert all(r.kind is not RowKind.CREATE for r in model.rows)

    def test_the_create_row_is_selectable_and_activating_it_opens_a_draft(
        self, sample, isolated_usage
    ):
        model = OverlayModel(shortcuts=sample)
        model.set_query("obsidian")
        assert model.current_row().kind is RowKind.CREATE
        assert model.activate() is True
        assert model.mode is Mode.FORM
        assert model.draft.is_new
        assert model.draft.command == "obsidian"

    def test_the_create_row_is_not_counted_as_a_binding(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.set_query("obsidian")
        assert model.count() == 0


class TestOpeningTheForm:
    def test_begin_edit_requires_a_selection(self, isolated_usage):
        model = OverlayModel(shortcuts=[])
        assert model.begin_edit() is False
        assert model.mode is Mode.BROWSE

    def test_begin_edit_seeds_the_draft_from_the_selection(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.set_query("firefox")
        assert model.begin_edit() is True
        assert model.mode is Mode.FORM
        assert model.draft.target is model.current()
        assert model.draft.chord == Chord.parse("Super+B")

    def test_begin_edit_unwraps_the_action_when_told_how(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.set_query("firefox")
        model.begin_edit(unwrap=lambda action: action.replace("spawn ", ""))
        assert model.draft.command == "firefox"
        assert model.draft.spawns is True

    def test_a_native_action_is_not_treated_as_a_spawn(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.set_query("close-window")
        model.begin_edit(unwrap=lambda action: None)
        assert model.draft.command == "close-window"
        assert model.draft.spawns is False

    def test_begin_add_is_independent_of_the_current_selection(
        self, sample, isolated_usage
    ):
        model = OverlayModel(shortcuts=sample)
        model.set_query("firefox")  # select Super+B
        assert model.begin_add() is True
        assert model.draft.target is None
        # Super+B is the selected row's own chord, but this is a *new*
        # binding, so nothing is excluded from the conflict check.
        model.draft.capture(Chord.parse("Super+B"))
        assert model.draft.claimant() is not None

    def test_begin_add_works_with_nothing_selected(self, isolated_usage):
        model = OverlayModel(shortcuts=[])
        assert model.begin_add() is True
        assert model.mode is Mode.FORM

    def test_begin_delete_asks_for_confirmation(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.begin_delete()
        assert model.mode is Mode.CONFIRM_DELETE
        assert model.delete_target is model.current()

    def test_cancel_returns_to_browse_and_drops_the_draft(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        model.begin_add()
        model.cancel()
        assert model.mode is Mode.BROWSE
        assert model.draft is None
        assert model.delete_target is None
        assert model.status == ""


class TestHintsAndHeader:
    def test_hint_changes_per_mode(self, sample, isolated_usage):
        model = OverlayModel(shortcuts=sample)
        browse_hint = model.hint()
        model.begin_add()
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
