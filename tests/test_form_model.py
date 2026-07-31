"""The add/edit form's state machine, tested without GTK.

This is the part the previous overlay got wrong -- modal capture that ate the
keys you needed to escape it, and an add path that happily wrote a duplicate
binding on top of a chord something else already owned. Both behaviours are
pinned here.
"""

from pathlib import Path

import pytest

from cachy_shortcuts.appscan import DesktopApp
from cachy_shortcuts.model import Category, Chord, Shortcut, SourceRef
from cachy_shortcuts.ui.form_model import BindingDraft, Field, suggest_chord


def make(chord_text, action, description="", category=Category.LAUNCH):
    return Shortcut(
        chord=Chord.parse(chord_text),
        action=action,
        description=description,
        category=category,
        source=SourceRef("niri", Path("/tmp/x.kdl"), 0, 1, 1),
    )


@pytest.fixture
def existing():
    return [
        make("Super+Return", "spawn alacritty", "Terminal"),
        make("Super+B", "spawn firefox", "Browser"),
        make("Super+Q", "close-window"),
    ]


@pytest.fixture
def apps():
    return [
        DesktopApp(name="Obsidian", command="obsidian", desktop_id="md.obsidian"),
        DesktopApp(name="Firefox", command="firefox", desktop_id="firefox"),
        DesktopApp(name="Files", command="nautilus", desktop_id="org.gnome.Nautilus"),
    ]


class TestChordSuggestion:
    def test_prefers_super_plus_the_first_letter(self, existing):
        assert suggest_chord("Obsidian", existing) == Chord.parse("Super+O")

    def test_skips_a_letter_whose_chord_is_taken(self, existing):
        # Super+B belongs to the browser, so Bitwarden gets the next letter.
        assert suggest_chord("Bitwarden", existing) == Chord.parse("Super+I")

    def test_reaches_for_a_second_modifier_only_when_needed(self):
        taken = [make(f"Super+{c}", "x") for c in "abc"]
        assert suggest_chord("cab", taken) == Chord.parse("Super+Shift+C")

    def test_digits_are_usable(self, existing):
        assert suggest_chord("7zip", existing) == Chord.parse("Super+7")

    def test_a_nameless_app_suggests_nothing(self, existing):
        assert suggest_chord("", existing) is None
        assert suggest_chord("—", existing) is None


class TestFieldNavigation:
    def test_a_new_draft_starts_in_the_command_field(self, existing):
        # Command first: you know what you want to bind before you know which
        # keys are free.
        assert BindingDraft.for_new(existing).focus is Field.COMMAND

    def test_tab_cycles_forward_and_wraps(self, existing):
        draft = BindingDraft.for_new(existing)
        draft.focus_next()
        assert draft.focus is Field.CHORD
        draft.focus_next()
        assert draft.focus is Field.DESCRIPTION
        draft.focus_next()
        assert draft.focus is Field.COMMAND

    def test_shift_tab_cycles_backward(self, existing):
        draft = BindingDraft.for_new(existing)
        draft.focus_previous()
        assert draft.focus is Field.DESCRIPTION

    def test_arriving_at_an_empty_chord_field_starts_listening(self, existing):
        draft = BindingDraft.for_new(existing)
        draft.focus_next()
        assert draft.focus is Field.CHORD
        assert draft.chord_armed is True

    def test_arriving_at_a_filled_chord_field_starts_listening_too(self, existing):
        """A chord already in the field is a default, not a decision."""
        draft = BindingDraft.for_new(existing)
        draft.chord = Chord.parse("Super+Z")
        draft.focus_next()
        assert draft.chord_armed is True


class TestChordCapture:
    def test_capturing_stops_the_listening(self, existing):
        """The whole point: after a chord lands, Tab/Enter/Esc work again."""
        draft = BindingDraft.for_new(existing)
        draft.focus_field(Field.CHORD)
        assert draft.chord_armed is True
        draft.capture(Chord.parse("Super+Y"))
        assert draft.chord == Chord.parse("Super+Y")
        assert draft.chord_armed is False

    def test_clearing_re_arms(self, existing):
        draft = BindingDraft.for_new(existing)
        draft.capture(Chord.parse("Super+Y"))
        draft.clear_chord()
        assert draft.chord is None
        assert draft.chord_armed is True

    def test_disarming_keeps_the_chord(self, existing):
        draft = BindingDraft.for_new(existing)
        draft.capture(Chord.parse("Super+Y"))
        draft.arm_chord()
        draft.disarm_chord()
        assert draft.chord == Chord.parse("Super+Y")

    def test_chord_text_explains_each_state(self, existing):
        draft = BindingDraft.for_new(existing)
        assert draft.chord_text() == "not set"
        draft.arm_chord()
        assert "press" in draft.chord_text()
        draft.capture(Chord.parse("Super+Y"))
        assert draft.chord_text() == "SUPER + Y"

    def test_recapturing_withdraws_a_replace_confirmation(self, existing):
        """Agreeing to take Super+B must not carry over to a different chord."""
        draft = BindingDraft.for_new(existing)
        draft.capture(Chord.parse("Super+B"))
        draft.confirm_replace()
        draft.capture(Chord.parse("Super+Z"))
        assert draft.replace_confirmed is False


class TestAppSuggestions:
    def test_typing_filters_installed_apps(self, existing, apps):
        draft = BindingDraft.for_new(existing, apps)
        draft.set_command("obs")
        assert [a.name for a in draft.suggestions()] == ["Obsidian"]

    def test_no_suggestions_for_an_empty_field(self, existing, apps):
        assert BindingDraft.for_new(existing, apps).suggestions() == []

    def test_suggestions_are_hidden_outside_the_command_field(self, existing, apps):
        draft = BindingDraft.for_new(existing, apps)
        draft.set_command("obs")
        draft.focus_field(Field.CHORD)
        assert draft.suggestions() == []

    def test_a_command_line_suppresses_the_type_ahead(self, existing, apps):
        """`firefox --private-window` is a command, not a half-typed name."""
        draft = BindingDraft.for_new(existing, apps)
        draft.set_command("firefox --private-window")
        assert draft.suggestions() == []
        draft.set_command("/usr/bin/firefox")
        assert draft.suggestions() == []

    def test_moving_through_suggestions_wraps(self, existing, apps):
        draft = BindingDraft.for_new(existing, apps)
        draft.set_command("f")  # Firefox, Files
        first = draft.selected_suggestion().name
        draft.move_suggestion(1)
        assert draft.selected_suggestion().name != first
        draft.move_suggestion(1)
        assert draft.selected_suggestion().name == first

    def test_accepting_fills_command_description_and_a_free_chord(self, existing, apps):
        draft = BindingDraft.for_new(existing, apps)
        draft.set_command("obs")
        assert draft.accept_suggestion() is True
        assert draft.command == "obsidian"
        assert draft.description == "Obsidian"
        assert draft.chord == Chord.parse("Super+O")

    def test_a_suggested_chord_can_be_typed_over(self, existing, apps):
        """The reported bug: the form assigned a chord and then wouldn't budge.

        Tab from the command field fills the chord in, so the field it lands on
        has to be listening -- otherwise the suggestion is a decision made for
        you rather than a default offered to you.
        """
        draft = BindingDraft.for_new(existing, apps)
        draft.set_command("obs")
        draft.accept_suggestion()
        draft.focus_field(Field.CHORD)
        assert draft.chord == Chord.parse("Super+O")
        assert draft.chord_armed is True
        assert "press to change" in draft.chord_text()

        draft.capture(Chord.parse("Ctrl+Alt+T"))
        assert draft.chord == Chord.parse("Ctrl+Alt+T")
        assert draft.chord_armed is False
        assert draft.chord_text() == draft.chord.display()

    def test_the_suggested_chord_is_never_one_already_taken(self, existing, apps):
        draft = BindingDraft.for_new(existing, apps)
        draft.set_command("firefox")
        draft.accept_suggestion()
        assert draft.claimant() is None

    def test_accepting_never_overwrites_a_chord_you_pressed(self, existing, apps):
        draft = BindingDraft.for_new(existing, apps)
        draft.capture(Chord.parse("Super+Z"))
        draft.set_command("obs")
        draft.accept_suggestion()
        assert draft.chord == Chord.parse("Super+Z")

    def test_accepting_nothing_reports_false(self, existing, apps):
        draft = BindingDraft.for_new(existing, apps)
        draft.set_command("zzzz")
        assert draft.accept_suggestion() is False

    def test_editing_the_command_resets_the_highlight(self, existing, apps):
        draft = BindingDraft.for_new(existing, apps)
        draft.set_command("f")
        draft.move_suggestion(1)
        draft.set_command("fi")
        assert draft.suggestion_index == 0


class TestConflicts:
    def test_a_free_chord_has_no_claimant(self, existing):
        draft = BindingDraft.for_new(existing)
        draft.capture(Chord.parse("Super+Z"))
        assert draft.claimant() is None
        assert draft.conflict_message() is None

    def test_a_taken_chord_names_its_owner(self, existing):
        draft = BindingDraft.for_new(existing)
        draft.capture(Chord.parse("Super+B"))
        assert draft.claimant().description == "Browser"
        assert "Browser" in draft.conflict_message()

    def test_a_binding_does_not_conflict_with_itself(self, existing):
        target = existing[1]  # Super+B
        draft = BindingDraft.for_edit(existing, target, "firefox")
        assert draft.claimant() is None

    def test_editing_can_still_collide_with_a_different_binding(self, existing):
        draft = BindingDraft.for_edit(existing, existing[1], "firefox")
        draft.capture(Chord.parse("Super+Q"))
        assert draft.claimant() is not None

    def test_a_disabled_binding_does_not_claim_its_chord(self):
        disabled = make("Super+B", "spawn firefox")
        disabled.extras["disabled"] = True
        draft = BindingDraft.for_new([disabled])
        draft.capture(Chord.parse("Super+B"))
        assert draft.claimant() is None


class TestValidation:
    def test_a_complete_free_draft_saves(self, existing, apps):
        draft = BindingDraft.for_new(existing, apps)
        draft.set_command("obsidian")
        draft.capture(Chord.parse("Super+O"))
        assert draft.can_save()
        assert draft.blockers() == []

    def test_a_missing_command_blocks(self, existing):
        draft = BindingDraft.for_new(existing)
        draft.capture(Chord.parse("Super+O"))
        assert not draft.can_save()
        assert any("command" in b for b in draft.blockers())

    def test_a_missing_chord_blocks(self, existing):
        draft = BindingDraft.for_new(existing)
        draft.set_command("obsidian")
        assert not draft.can_save()
        assert any("key combination" in b for b in draft.blockers())

    def test_a_claimed_chord_blocks_until_confirmed(self, existing):
        """The bug this replaces: adding used to write a silent duplicate."""
        draft = BindingDraft.for_new(existing)
        draft.set_command("obsidian")
        draft.capture(Chord.parse("Super+B"))
        assert not draft.can_save()

        assert draft.confirm_replace() is True
        assert draft.can_save()
        assert "will replace" in draft.conflict_message()

    def test_confirming_without_a_conflict_is_a_no_op(self, existing):
        draft = BindingDraft.for_new(existing)
        draft.capture(Chord.parse("Super+Z"))
        assert draft.confirm_replace() is False
        assert draft.replace_confirmed is False

    def test_a_command_not_on_path_warns_but_does_not_block(self, existing, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _name: None)
        draft = BindingDraft.for_new(existing)
        draft.set_command("obsidian")
        draft.capture(Chord.parse("Super+O"))
        assert draft.can_save()
        assert any("PATH" in w for w in draft.warnings())

    def test_a_command_on_path_does_not_warn(self, existing, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/obsidian")
        draft = BindingDraft.for_new(existing)
        draft.set_command("obsidian")
        assert draft.warnings() == []

    def test_a_native_action_is_never_path_checked(self, existing, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _name: None)
        draft = BindingDraft.for_edit(existing, existing[2], None)
        assert draft.spawns is False
        assert draft.warnings() == []

    def test_a_shell_snippet_is_not_path_checked(self, existing, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _name: None)
        draft = BindingDraft.for_new(existing)
        draft.set_command("FOO=1 something")
        assert draft.warnings() == []

    def test_an_unbalanced_quote_does_not_crash_validation(self, existing):
        draft = BindingDraft.for_new(existing)
        draft.set_command('sh -c "echo hi')
        draft.warnings()  # must not raise


class TestEditingSeed:
    def test_edit_seeds_every_field_from_the_target(self, existing):
        draft = BindingDraft.for_edit(existing, existing[1], "firefox")
        assert draft.command == "firefox"
        assert draft.chord == Chord.parse("Super+B")
        assert draft.description == "Browser"
        assert draft.spawns is True
        assert draft.is_new is False
        assert draft.title == "Edit binding"

    def test_a_native_action_is_shown_verbatim(self, existing):
        draft = BindingDraft.for_edit(existing, existing[2], None)
        assert draft.command == "close-window"
        assert draft.spawns is False

    def test_a_new_draft_can_be_pre_filled(self, existing):
        draft = BindingDraft.for_new(existing, command="obsidian")
        assert draft.command == "obsidian"
        assert draft.is_new
        assert draft.title == "New binding"


class TestHints:
    def test_the_hint_follows_the_state(self, existing, apps):
        draft = BindingDraft.for_new(existing, apps)
        draft.set_command("obs")
        assert "pick app" in draft.hint()

        draft.focus_field(Field.CHORD)
        assert "press a combination" in draft.hint()

        draft.capture(Chord.parse("Super+B"))
        assert "take the chord" in draft.hint()

        draft.confirm_replace()
        assert draft.hint() == "tab next field · enter save · esc cancel"
