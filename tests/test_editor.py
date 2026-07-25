"""Write-path tests.

These are the tests that earn the right to edit someone's live compositor
config: round-trip fidelity, comment preservation, and automatic rollback.
"""

import shutil

import pytest

from cachy_shortcuts import backup, editor
from cachy_shortcuts.backends import CosmicBackend, MangoBackend, NiriBackend
from cachy_shortcuts.model import Chord

from .conftest import FIXTURES, by_chord


@pytest.fixture(autouse=True)
def isolated_backups(tmp_path, monkeypatch):
    """Keep snapshots out of the real ~/.local/share during tests."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


@pytest.fixture
def niri_rw(tmp_path):
    root = tmp_path / "niri"
    shutil.copytree(FIXTURES / "niri", root)
    return NiriBackend(config_root=root)


@pytest.fixture
def mango_rw(tmp_path):
    root = tmp_path / "mango"
    shutil.copytree(FIXTURES / "mango", root)
    return MangoBackend(config_root=root)


@pytest.fixture
def cosmic_rw(tmp_path):
    root = tmp_path / "cosmic"
    shutil.copytree(FIXTURES / "cosmic", root)
    return CosmicBackend(config_root=root / "config", system_root=root / "system")


class TestRoundTripFidelity:
    """Re-rendering an unchanged binding must reproduce it exactly."""

    def test_niri_single_line_binds_are_byte_identical(self, niri_rw):
        for shortcut in niri_rw.read():
            if "\n" in shortcut.raw:
                continue  # multi-line bodies are deliberately collapsed
            rendered = niri_rw.render(
                shortcut.chord,
                shortcut.action,
                shortcut.description,
                shortcut.extras,
            )
            assert rendered == shortcut.raw

    def test_niri_preserves_camel_case_key_spelling(self, niri_rw):
        found = by_chord(niri_rw.read())
        rendered = niri_rw.render(
            found["super+wheelscrolldown"].chord,
            found["super+wheelscrolldown"].action,
            extras=found["super+wheelscrolldown"].extras,
        )
        assert "WheelScrollDown" in rendered

    def test_mango_binds_are_byte_identical(self, mango_rw):
        for shortcut in mango_rw.read():
            rendered = mango_rw.render(
                shortcut.chord, shortcut.action, extras=shortcut.extras
            )
            assert rendered == shortcut.raw

    def test_mango_preserves_raw_keycodes(self, mango_rw):
        found = by_chord(mango_rw.read())
        target = found["code:133+code:64+code:24"]
        rendered = mango_rw.render(target.chord, target.action, extras=target.extras)
        assert rendered == "bind=code:64+code:133,code:24,killclient,"

    def test_mango_preserves_bind_flags(self, mango_rw):
        found = by_chord(mango_rw.read())
        rendered = mango_rw.render(
            found["super+l"].chord, found["super+l"].action, extras=found["super+l"].extras
        )
        assert rendered.startswith("bindl=")


class TestSurgicalWrites:
    def test_rebind_leaves_comments_intact(self, niri_rw):
        path = niri_rw.config_paths()[0]
        before = path.read_text()
        target = by_chord(niri_rw.read())["super+b"]
        editor.rebind(niri_rw, target, Chord.parse("Super+Shift+B"))
        after = path.read_text()

        for comment in ("// ─── Applications ───", "// ─── Media ───", "// ─── Windows ───"):
            assert comment in after
        # Only the one binding changed.
        changed = [
            (a, b)
            for a, b in zip(before.splitlines(), after.splitlines())
            if a != b
        ]
        assert len(changed) == 1
        assert "Mod+Shift+B" in changed[0][1]

    def test_rebind_preserves_other_properties(self, niri_rw):
        target = by_chord(niri_rw.read())["xf86audioraisevolume"]
        editor.rebind(niri_rw, target, Chord.parse("Super+Up"))
        after = by_chord(niri_rw.read())
        assert after["super+up"].extras["props"]["allow-when-locked"] == "true"
        assert '"5%+"' in after["super+up"].action

    def test_add_lands_inside_the_binds_block(self, niri_rw):
        editor.add(niri_rw, Chord.parse("Super+N"), 'spawn "obsidian"', "Notes")
        found = by_chord(niri_rw.read())
        assert found["super+n"].description == "Notes"
        assert found["super+n"].source.path.name == "keybinds.kdl"

    def test_add_to_mango_appends_after_last_bind(self, mango_rw):
        editor.add(mango_rw, Chord.parse("Super+N"), "spawn obsidian")
        text = mango_rw.config_paths()[0].read_text()
        assert "bind=SUPER,n,spawn,obsidian" in text
        # The trailing comment block must not have been displaced.
        assert text.count("# System") == 1

    def test_delete_removes_the_whole_line(self, mango_rw):
        target = by_chord(mango_rw.read())["super+g"]
        path = target.source.path
        editor.delete(mango_rw, target)
        text = path.read_text()
        assert "gimp" not in text
        assert "\n\n\n" not in text  # no blank gap left behind
        assert "super+g" not in by_chord(mango_rw.read())

    def test_retarget_changes_only_the_command(self, mango_rw):
        target = by_chord(mango_rw.read())["super+b"]
        editor.retarget(mango_rw, target, "spawn chromium")
        found = by_chord(mango_rw.read())
        assert found["super+b"].action == "spawn chromium"
        assert found["super+b"].chord == Chord.parse("Super+B")

    def test_editing_is_idempotent_across_a_full_cycle(self, niri_rw):
        """Reading everything and writing it back unchanged is a no-op."""
        path = niri_rw.config_paths()[0]
        before = path.read_text()
        # Re-read between edits, as the UI does: a write invalidates the spans
        # of every binding after it in the file.
        for index in range(len(niri_rw.read())):
            shortcut = niri_rw.read()[index]
            if "\n" in shortcut.raw:
                continue
            editor.relabel(niri_rw, shortcut, shortcut.description)
        assert path.read_text() == before

    def test_hidden_binding_stays_hidden(self, niri_rw):
        """`hotkey-overlay-title=null` hides a bind from niri's own overlay."""
        target = by_chord(niri_rw.read())["super+space"]
        assert target.extras["title_null"] is True
        editor.retarget(niri_rw, target, 'spawn-sh "qs -c noctalia-shell ipc call launcher open"')
        assert "hotkey-overlay-title=null" in niri_rw.config_paths()[0].read_text()


class TestCosmicOverrides:
    def test_editing_a_default_writes_an_override_to_custom(self, cosmic_rw):
        target = by_chord(cosmic_rw.read())["super+q"]
        assert target.extras["readonly"] is True
        system_before = cosmic_rw._defaults.read_text()

        result = editor.rebind(cosmic_rw, target, Chord.parse("Super+Shift+Q"))

        assert result.path == cosmic_rw._custom
        # The system file must be untouched.
        assert cosmic_rw._defaults.read_text() == system_before
        assert "super+shift+q" in by_chord(cosmic_rw.read())

    def test_deleting_a_default_records_a_disable(self, cosmic_rw):
        target = by_chord(cosmic_rw.read())["super+escape"]
        editor.delete(cosmic_rw, target)
        assert "Disable" in cosmic_rw._custom.read_text()
        assert "super+escape" not in by_chord(cosmic_rw.read())

    def test_deleting_a_custom_binding_removes_it_outright(self, cosmic_rw):
        target = by_chord(cosmic_rw.read())["super+b"]
        assert target.extras["readonly"] is False
        editor.delete(cosmic_rw, target)
        assert "firefox" not in cosmic_rw._custom.read_text()


class TestSafety:
    def test_snapshot_is_taken_before_every_write(self, niri_rw):
        assert backup.list_snapshots() == []
        target = by_chord(niri_rw.read())["super+b"]
        editor.rebind(niri_rw, target, Chord.parse("Super+Shift+B"))
        assert len(backup.list_snapshots()) == 1

    def test_undo_restores_the_previous_content(self, niri_rw):
        path = niri_rw.config_paths()[0]
        before = path.read_text()
        target = by_chord(niri_rw.read())["super+b"]
        editor.rebind(niri_rw, target, Chord.parse("Super+Shift+B"))
        assert path.read_text() != before

        editor.undo_last()
        assert path.read_text() == before

    def test_stale_span_is_refused(self, niri_rw):
        target = by_chord(niri_rw.read())["super+b"]
        # Simulate the file changing underneath us between read and write.
        path = target.source.path
        path.write_text("// clobbered\n" + path.read_text())
        with pytest.raises(editor.EditError, match="changed since it was read"):
            editor.rebind(niri_rw, target, Chord.parse("Super+Shift+B"))

    def test_failed_validation_rolls_back(self, niri_rw, monkeypatch):
        path = niri_rw.config_paths()[0]
        before = path.read_text()
        target = by_chord(niri_rw.read())["super+b"]

        # Render something that parses but is not the requested binding.
        monkeypatch.setattr(
            niri_rw, "render", lambda *a, **k: 'Mod+Z { spawn "wrong"; }'
        )
        with pytest.raises(editor.EditError, match="did not take effect"):
            editor.rebind(niri_rw, target, Chord.parse("Super+Shift+B"))
        assert path.read_text() == before

    def test_unparsable_output_rolls_back(self, niri_rw, monkeypatch):
        path = niri_rw.config_paths()[0]
        before = path.read_text()
        target = by_chord(niri_rw.read())["super+b"]

        def explode(*args, **kwargs):
            raise ValueError("boom")

        monkeypatch.setattr(niri_rw, "parse", explode)
        with pytest.raises(editor.EditError, match="rolled back"):
            editor.rebind(niri_rw, target, Chord.parse("Super+Shift+B"))
        assert path.read_text() == before

    def test_atomic_write_leaves_no_temp_files(self, niri_rw):
        target = by_chord(niri_rw.read())["super+b"]
        editor.rebind(niri_rw, target, Chord.parse("Super+Shift+B"))
        leftovers = list(target.source.path.parent.glob(".*tmp"))
        assert leftovers == []

    def test_prune_keeps_the_cap(self, niri_rw):
        for _ in range(5):
            backup.create([niri_rw.config_paths()[0]], reason="test")
        assert backup.prune(keep=2) == 3
        assert len(backup.list_snapshots()) == 2


class TestCommandWrapping:
    """wrap_command_as_action is the single place commands get escaped before
    being embedded in a backend's own quoting syntax."""

    def test_niri_wraps_in_spawn_sh(self, niri_rw):
        assert editor.wrap_command_as_action(niri_rw, "firefox") == 'spawn-sh "firefox"'

    def test_niri_escapes_embedded_quotes(self, niri_rw):
        action = editor.wrap_command_as_action(niri_rw, 'echo "hi"')
        assert action == 'spawn-sh "echo \\"hi\\""'
        # Adding it must produce valid KDL: re-parsing recovers the exact
        # same bind rather than truncating at the embedded quote.
        result = editor.add(niri_rw, Chord.parse("Super+Z"), action)
        reparsed = niri_rw.parse(result.path.read_text(), result.path)
        target = next(s for s in reparsed if s.chord == Chord.parse("Super+Z"))
        assert target.action == action

    def test_mango_wraps_in_spawn(self, mango_rw):
        assert editor.wrap_command_as_action(mango_rw, "firefox") == "spawn firefox"

    def test_cosmic_leaves_bare_commands_for_render_to_wrap(self, cosmic_rw):
        assert editor.wrap_command_as_action(cosmic_rw, "firefox") == "firefox"

    def test_cosmic_render_escapes_embedded_quotes(self, cosmic_rw):
        rendered = cosmic_rw.render(Chord.parse("Super+Z"), 'echo "hi"')
        assert rendered == '(modifiers: [Super], key: "z"): Spawn("echo \\"hi\\""),'


class TestUnwrapAction:
    """The inverse of wrap_command_as_action, so the edit form can show you
    `firefox` instead of `spawn-sh "firefox"` -- and so that saving a native
    compositor action back doesn't wrap it into `spawn-sh "close-window"`."""

    def test_niri_spawn_sh_round_trips(self, niri_rw):
        action = editor.wrap_command_as_action(niri_rw, "firefox --new-window")
        assert editor.unwrap_action(niri_rw, action) == "firefox --new-window"

    def test_niri_multi_token_spawn_becomes_a_command_line(self, niri_rw):
        action = 'spawn "wpctl" "set-volume" "@DEFAULT_AUDIO_SINK@" "5%+"'
        unwrapped = editor.unwrap_action(niri_rw, action)
        assert unwrapped == "wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%+"

    def test_niri_native_actions_are_not_spawns(self, niri_rw):
        assert editor.unwrap_action(niri_rw, "close-window") is None
        assert editor.unwrap_action(niri_rw, "focus-workspace 1") is None
        assert editor.unwrap_action(niri_rw, "show-hotkey-overlay") is None

    def test_mango_round_trips(self, mango_rw):
        action = editor.wrap_command_as_action(mango_rw, "firefox")
        assert editor.unwrap_action(mango_rw, action) == "firefox"
        assert editor.unwrap_action(mango_rw, "quit") is None

    def test_cosmic_unwraps_its_spawn_form(self, cosmic_rw):
        assert editor.unwrap_action(cosmic_rw, 'Spawn("firefox")') == "firefox"
        assert editor.unwrap_action(cosmic_rw, 'Spawn("echo \\"hi\\"")') == 'echo "hi"'
        assert editor.unwrap_action(cosmic_rw, "Move(Left)") is None

    def test_an_unbalanced_quote_does_not_raise(self, niri_rw):
        assert editor.unwrap_action(niri_rw, 'spawn-sh "echo hi') is not None


class TestUpdate:
    """One write for chord + action + description together.

    Doing it as three calls would be wrong, not merely wasteful: the first
    write moves every span after it, so the second would edit the wrong bytes.
    """

    def test_all_three_change_in_a_single_write(self, niri_rw):
        target = by_chord(niri_rw.read())["super+b"]
        editor.update(
            niri_rw,
            target,
            chord=Chord.parse("Super+Shift+W"),
            action='spawn-sh "chromium"',
            description="Web",
        )
        updated = by_chord(niri_rw.read())["super+shift+w"]
        assert updated.action == 'spawn-sh "chromium"'
        assert updated.description == "Web"
        assert "super+b" not in by_chord(niri_rw.read())

    def test_omitted_fields_are_left_alone(self, niri_rw):
        target = by_chord(niri_rw.read())["super+b"]
        editor.update(niri_rw, target, chord=Chord.parse("Super+Shift+W"))
        updated = by_chord(niri_rw.read())["super+shift+w"]
        assert updated.action == target.action
        assert updated.description == target.description

    def test_neighbouring_bindings_are_untouched(self, niri_rw):
        before = niri_rw.config_paths()[0].read_text()
        target = by_chord(niri_rw.read())["super+b"]
        editor.update(niri_rw, target, description="Web")
        after = niri_rw.config_paths()[0].read_text()
        for line in before.splitlines():
            if "Mod+B " not in line:
                assert line in after


class TestTakeOver:
    """Claiming a chord something else owns must unbind the old one, not
    append a duplicate the compositor will silently ignore."""

    def test_a_new_binding_replaces_the_old_claimant(self, niri_rw):
        victim = by_chord(niri_rw.read())["super+b"]
        editor.take_over(
            niri_rw, victim, None, Chord.parse("Super+B"), 'spawn-sh "chromium"', "Web"
        )
        after = niri_rw.read()
        claimants = [s for s in after if s.chord == Chord.parse("Super+B")]
        assert len(claimants) == 1
        assert claimants[0].action == 'spawn-sh "chromium"'

    def test_an_existing_binding_can_take_another_chord(self, niri_rw):
        shortcuts = by_chord(niri_rw.read())
        victim, target = shortcuts["super+b"], shortcuts["super+e"]
        editor.take_over(
            niri_rw, victim, target, Chord.parse("Super+B"), target.action, "Files"
        )
        after = by_chord(niri_rw.read())
        assert "super+e" not in after
        assert after["super+b"].action == target.action
        assert after["super+b"].description == "Files"

    def test_the_target_is_relocated_after_the_delete_shifts_spans(self, niri_rw):
        """The victim sits *before* the target in the file, so every span
        after it moves. Using the stale record would edit the wrong bytes."""
        shortcuts = by_chord(niri_rw.read())
        victim, target = shortcuts["super+return"], shortcuts["super+shift+e"]
        editor.take_over(
            niri_rw, victim, target, Chord.parse("Super+Return"), target.action
        )
        after = by_chord(niri_rw.read())
        assert after["super+return"].action == "quit"
        assert "super+shift+e" not in after

    def test_a_target_that_vanished_is_reported_rather_than_guessed(self, niri_rw):
        victim = by_chord(niri_rw.read())["super+b"]
        ghost = by_chord(niri_rw.read())["super+e"]
        ghost.action = "spawn \"something-that-is-not-in-the-file\""
        with pytest.raises(editor.EditError, match="could not be found again"):
            editor.take_over(niri_rw, victim, ghost, Chord.parse("Super+B"), "x")


class TestWriteFile:
    """The snapshot/atomic/validate/rollback path for non-binding files."""

    def test_a_new_file_is_written_and_validated(self, tmp_path):
        path = tmp_path / "new" / "rules"
        editor.write_file(path, "hello\n", "test", lambda text: "hello" in text)
        assert path.read_text() == "hello\n"

    def test_a_failed_validation_removes_a_file_it_created(self, tmp_path):
        path = tmp_path / "new" / "rules"
        with pytest.raises(editor.EditError, match="rolled back"):
            editor.write_file(path, "hello\n", "test", lambda text: False)
        assert not path.exists()

    def test_a_failed_validation_restores_previous_content(self, tmp_path):
        path = tmp_path / "rules"
        path.write_text("original\n")
        with pytest.raises(editor.EditError, match="rolled back"):
            editor.write_file(path, "replacement\n", "test", lambda text: False)
        assert path.read_text() == "original\n"

    def test_the_write_is_undoable(self, tmp_path):
        path = tmp_path / "rules"
        path.write_text("original\n")
        editor.write_file(path, "changed\n", "test", lambda text: True)
        assert path.read_text() == "changed\n"
        editor.undo_last()
        assert path.read_text() == "original\n"
