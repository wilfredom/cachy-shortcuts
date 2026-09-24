"""Write-path tests.

These are the tests that earn the right to edit someone's live compositor
config: round-trip fidelity, comment preservation, and automatic rollback.
"""

import shutil

import pytest

from cachy_shortcuts import backup, editor
from cachy_shortcuts.backends import (
    CosmicBackend,
    HyprlandBackend,
    MangoBackend,
    NiriBackend,
)
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
def hypr_rw(tmp_path):
    root = tmp_path / "hypr"
    shutil.copytree(FIXTURES / "hyprland", root)
    return HyprlandBackend(config_root=root)


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

    def test_mango_edit_keeps_the_lines_spacing(self, mango_rw):
        target = by_chord(mango_rw.read())["super+r"]
        editor.retarget(mango_rw, target, "spawn foot")
        text = target.source.path.read_text()
        assert "bind = SUPER, r, spawn, foot\n" in text

    @pytest.mark.parametrize(
        "line, action, expected",
        [
            ("bind = SUPER, b, spawn, firefox", "killclient", "bind = SUPER, b, killclient,"),
            ("bind = SUPER, q, killclient,", "spawn foot", "bind = SUPER, q, spawn, foot"),
            ("bind=SUPER,q,killclient,", "spawn foot", "bind=SUPER,q,spawn,foot"),
            ("bind = SUPER, r, reload_config", "spawn foot", "bind = SUPER, r, spawn, foot"),
        ],
    )
    def test_mango_render_follows_the_lines_spacing(self, mango_rw, line, action, expected):
        path = mango_rw.config_paths()[0]
        (shortcut,) = mango_rw.parse(line + "\n", path)
        assert mango_rw.render(shortcut.chord, action, extras=shortcut.extras) == expected

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


    def test_hyprland_binds_are_byte_identical(self, hypr_rw):
        for shortcut in hypr_rw.read():
            rendered = hypr_rw.render(
                shortcut.chord,
                shortcut.action,
                shortcut.description,
                shortcut.extras,
            )
            assert rendered == shortcut.raw

    def test_hyprland_keeps_the_config_variable(self, hypr_rw):
        """An edit must not bake `$mainMod` down to the SUPER it expands to."""
        found = by_chord(hypr_rw.read())
        target = found["super+return"]
        rendered = hypr_rw.render(target.chord, "exec kitty", extras=target.extras)
        assert rendered == "bind = $mainMod, Return, exec, kitty"

    def test_hyprland_preserves_bind_flags(self, hypr_rw):
        found = by_chord(hypr_rw.read())
        target = found["super+l"]
        rendered = hypr_rw.render(target.chord, target.action, extras=target.extras)
        assert rendered.startswith("bindl = ")

    def test_hyprland_description_round_trips_as_bindd(self, hypr_rw):
        found = by_chord(hypr_rw.read())
        target = found["super+b"]
        rendered = hypr_rw.render(target.chord, target.action, "Web browser", target.extras)
        assert rendered == "bindd = $mainMod, B, Web browser, exec, $browser"

    def test_hyprland_description_cannot_smuggle_in_a_field_separator(self, hypr_rw):
        """A comma is this grammar's separator, so one in a description would
        turn the rest of it into a dispatcher."""
        result = editor.add(
            hypr_rw, Chord.parse("Super+Z"), "exec obsidian", "Notes, and more"
        )
        reparsed = hypr_rw.parse(result.path.read_text(), result.path)
        target = next(s for s in reparsed if s.chord == Chord.parse("Super+Z"))
        assert target.action == "exec obsidian"
        assert target.description == "Notes; and more"

    def test_hyprland_dropping_a_description_drops_the_d_flag(self, hypr_rw):
        found = by_chord(hypr_rw.read())
        target = found["super+b"]
        rendered = hypr_rw.render(target.chord, target.action, "", target.extras)
        assert rendered.startswith("bind = ")
        assert "Web browser" not in rendered

    def test_hyprland_adding_params_to_a_bare_dispatcher_spaces_the_comma(self, hypr_rw):
        """`killactive,` re-rendered with a command must not read `exec,firefox`."""
        target = next(
            s for s in hypr_rw.read() if s.chord.canonical == "super+q" and not s.extras["submap"]
        )
        rendered = hypr_rw.render(target.chord, "exec firefox", extras=target.extras)
        assert rendered == "bind = $mainMod, Q, exec, firefox"


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

    def test_add_to_mango_stays_out_of_the_keymode(self, mango_rw):
        """Appended after the resize binds, a new bind would only fire in
        resize mode."""
        editor.add(mango_rw, Chord.parse("Super+N"), "spawn obsidian")
        text = mango_rw.config_paths()[0].read_text()
        assert text.index("bind=SUPER,n,spawn,obsidian") < text.index("keymode=resize")
        assert by_chord(mango_rw.read())["super+n"].extras["submap"] == ""

    def test_add_to_a_keymode_only_mango_config_stays_global(self, mango_rw):
        path = mango_rw.config_paths()[0]
        path.write_text(
            "keymode=common\nbind=SUPER,r,reload_config\n"
            "keymode=resize\nbind=NONE,h,resizewin,-10,0\n"
        )
        editor.add(mango_rw, Chord.parse("Super+N"), "spawn obsidian")
        found = by_chord(mango_rw.read())
        assert found["super+n"].extras["keymode"] == "default"
        assert path.read_text().startswith("bind=SUPER,n,spawn,obsidian\n")

    def test_editing_a_commented_mango_bind_keeps_the_comment(self, mango_rw):
        path = mango_rw.config_paths()[0]
        path.write_text(path.read_text() + "bind=SUPER,F9,spawn,foot  # terminal\n")
        target = by_chord(mango_rw.read())["super+f9"]
        editor.retarget(mango_rw, target, "spawn kitty")
        assert "bind=SUPER,F9,spawn,kitty  # terminal\n" in path.read_text()

    def test_add_to_hyprland_stays_out_of_the_submap(self, hypr_rw):
        """A new global bind appended inside a submap would only fire in it."""
        editor.add(hypr_rw, Chord.parse("Super+N"), "exec obsidian", "Notes")
        text = hypr_rw.config_paths()[0].read_text()
        added = text.index("bindd = $mainMod, N, Notes, exec, obsidian")
        assert added < text.index("submap = resize")
        assert by_chord(hypr_rw.read())["super+n"].extras["submap"] == ""

    def test_add_to_a_submap_only_hyprland_config_stays_global(self, hypr_rw, tmp_path):
        """With no global bind to sit beside, the new one goes above the submap."""
        path = hypr_rw.config_paths()[0]
        path.write_text(
            "submap = resize\nbind = , right, resizeactive, 10 0\nsubmap = reset\n"
        )
        editor.add(hypr_rw, Chord.parse("Super+N"), "exec obsidian")
        found = by_chord(hypr_rw.read())
        assert found["super+n"].extras["submap"] == ""
        assert found["right"].extras["submap"] == "resize"

    def test_add_to_hyprland_reuses_the_config_variable(self, hypr_rw):
        editor.add(hypr_rw, Chord.parse("Super+Shift+N"), "exec obsidian")
        text = hypr_rw.config_paths()[0].read_text()
        assert "bind = $mainMod SHIFT, N, exec, obsidian" in text

    def test_hyprland_delete_removes_the_whole_line(self, hypr_rw):
        target = by_chord(hypr_rw.read())["super+d"]
        path = target.source.path
        editor.delete(hypr_rw, target)
        text = path.read_text()
        assert "dash toggle" not in text
        assert "\n\n\n" not in text
        assert "super+d" not in by_chord(hypr_rw.read())

    def test_hyprland_retarget_changes_only_the_command(self, hypr_rw):
        path = hypr_rw.config_paths()[0]
        before = path.read_text()
        target = by_chord(hypr_rw.read())["super+h"]
        editor.retarget(hypr_rw, target, "movefocus r")
        after = path.read_text()
        changed = [
            (a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b
        ]
        assert len(changed) == 1
        assert changed[0][1] == "bind = $mainMod, H, movefocus, r"

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

    def test_delete_one_of_two_binds_on_the_same_chord(self, mango_rw):
        """What `conflicts` reports must be fixable: Will's lap2 config binds
        SUPER+ALT,Left twice, and deleting either copy used to roll back."""
        path = mango_rw.config_paths()[0]
        path.write_text(
            path.read_text()
            + "bind = SUPER+ALT, Left, focusmon, left\n"
            + "bind = SUPER+ALT, Left, tagmon, left\n"
        )
        chord = Chord.parse("Super+Alt+Left")
        first = next(s for s in mango_rw.read() if s.chord == chord)
        editor.delete(mango_rw, first)
        left = [s for s in mango_rw.read() if s.chord == chord]
        assert [s.extras["command"].strip() for s in left] == ["tagmon"]

    def test_cosmic_delete_keeps_a_second_entry_on_the_same_line(self, cosmic_rw):
        custom = cosmic_rw.write_target()
        custom.write_text(
            "{\n"
            '    (modifiers: [Super], key: "b"): Spawn("firefox"), '
            '(modifiers: [Super], key: "e"): Spawn("nautilus"),\n'
            "}\n"
        )
        editor.delete(cosmic_rw, by_chord(cosmic_rw.read())["super+b"])
        assert custom.read_text() == (
            '{\n    (modifiers: [Super], key: "e"): Spawn("nautilus"),\n}\n'
        )

    def test_niri_delete_keeps_a_semicolon_separated_sibling(self, niri_rw):
        path = niri_rw.config_paths()[0]
        path.write_text(
            "binds {\n"
            '    Mod+T { spawn "alacritty"; }; Mod+Y { spawn "firefox"; }\n'
            "    Mod+Q { close-window; } // the one to keep\n"
            "    Mod+W { close-window; } // goes with its bind\n"
            "}\n"
        )
        editor.delete(niri_rw, by_chord(niri_rw.read())["super+t"])
        editor.delete(niri_rw, by_chord(niri_rw.read())["super+w"])
        assert path.read_text() == (
            "binds {\n"
            '    Mod+Y { spawn "firefox"; }\n'
            "    Mod+Q { close-window; } // the one to keep\n"
            "}\n"
        )
        editor.delete(niri_rw, by_chord(niri_rw.read())["super+y"])
        assert set(by_chord(niri_rw.read())) == {"super+q"}

    def test_a_delete_that_takes_anything_else_rolls_back(self, mango_rw, monkeypatch):
        """The check compares every binding in the file, not just the
        victim's chord."""
        path = mango_rw.config_paths()[0]
        before = path.read_text()
        target = by_chord(mango_rw.read())["super+b"]
        # A deletion span that reaches into the next line.
        monkeypatch.setattr(
            type(mango_rw),
            "deletion_span",
            lambda self, text, start, end: (start, text.find("\n", end + 1) + 1),
        )
        with pytest.raises(editor.EditError, match="did not take effect"):
            editor.delete(mango_rw, target)
        assert path.read_text() == before

    def test_delete_a_global_bind_whose_chord_a_submap_reuses(self, hypr_rw):
        """The fixture binds Super+Q globally and inside `submap = resize`."""
        chord = Chord.parse("Super+Q")
        target = next(
            s for s in hypr_rw.read() if s.chord == chord and not s.extras["submap"]
        )
        editor.delete(hypr_rw, target)
        left = [s for s in hypr_rw.read() if s.chord == chord]
        assert [s.extras["submap"] for s in left] == ["resize"]

    def test_rebinding_keypad_enter_keeps_the_keypad_key(self, hypr_rw):
        target = by_chord(hypr_rw.read())["super+kp_enter"]
        editor.rebind(hypr_rw, target, Chord.parse("Super+Shift+KP_Enter"))
        text = target.source.path.read_text()
        assert "bind = $mainMod SHIFT, KP_Enter, exec, $terminal" in text

    def test_deleting_keypad_enter_leaves_return_alone(self, hypr_rw):
        editor.delete(hypr_rw, by_chord(hypr_rw.read())["super+kp_enter"])
        after = by_chord(hypr_rw.read())
        assert "super+kp_enter" not in after
        assert "super+return" in after

    def test_hyprland_line_with_trailing_whitespace_is_editable(self, hypr_rw):
        """The recorded span must equal `raw`, or every edit of the line is
        refused as "changed since it was read"."""
        path = hypr_rw.config_paths()[0]
        path.write_text(path.read_text() + "bind = $mainMod, F11, exec, kitty  \n")
        target = by_chord(hypr_rw.read())["super+f11"]
        editor.retarget(hypr_rw, target, "exec foot")
        assert by_chord(hypr_rw.read())["super+f11"].action == "exec foot"
        assert "bind = $mainMod, F11, exec, foot  \n" in path.read_text()

    def test_mango_line_with_trailing_whitespace_is_deletable(self, mango_rw):
        path = mango_rw.config_paths()[0]
        before = path.read_text()
        path.write_text(before + "bind=SUPER,F11,spawn,foot \n")
        editor.delete(mango_rw, by_chord(mango_rw.read())["super+f11"])
        assert path.read_text() == before

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


class TestMangoSystemConfig:
    """With no ~/.config/mango/config.conf, mango runs /etc/mango/config.conf.

    That file belongs to the package: an edit must go to a user copy of it,
    since a user config.conf holding only the new bind would replace the
    system one and drop every default bind.
    """

    @pytest.fixture
    def fresh(self, tmp_path):
        system = tmp_path / "etc" / "mango" / "config.conf"
        system.parent.mkdir(parents=True)
        system.write_text((FIXTURES / "mango" / "config.conf").read_text())
        root = tmp_path / "home" / ".config" / "mango"
        return MangoBackend(config_root=root, system_config=system), system

    def test_reads_what_mango_runs(self, fresh):
        backend, system = fresh
        assert backend.config_paths()[0] == system
        assert "super+b" in by_chord(backend.read())

    def test_add_writes_a_user_copy_and_leaves_etc_alone(self, fresh):
        backend, system = fresh
        before = system.read_text()
        result = editor.add(backend, Chord.parse("Super+Y"), "spawn firefox")
        assert result.path == backend.write_target()
        after = result.path.read_text()
        assert after.replace("\nbind=SUPER,y,spawn,firefox", "", 1) == before
        assert system.read_text() == before
        found = by_chord(backend.read())
        assert "super+y" in found and "super+b" in found

    def test_deleting_a_system_bind_writes_the_copy_without_it(self, fresh):
        backend, system = fresh
        before = system.read_text()
        result = editor.delete(backend, by_chord(backend.read())["super+b"])
        assert result.path == backend.write_target()
        assert system.read_text() == before
        found = by_chord(backend.read())
        assert "super+b" not in found and "super+return" in found

    def test_the_float_rule_targets_the_user_copy(self, fresh):
        from cachy_shortcuts import floatrule

        backend, system = fresh
        state = floatrule.install(backend)
        assert state.rule.path == backend.write_target()
        assert "super+b" in by_chord(backend.read())

    def test_undo_removes_the_copy_again(self, fresh):
        backend, system = fresh
        editor.add(backend, Chord.parse("Super+Y"), "spawn firefox")
        editor.undo_last()
        assert not backend.write_target().exists()

    def test_an_unreadable_system_config_refuses_the_edit(self, fresh):
        """Seeding nothing would create a user config holding only the new
        bind; mango would then read that instead of /etc and lose every
        system bind, while the edit reported success."""
        backend, system = fresh
        # One Latin-1 byte in a comment: mango's C parser does not care.
        system.write_bytes(b"# Konfiguration f\xfcr mango\n" + system.read_bytes())
        with pytest.raises(editor.EditError, match="cannot create"):
            editor.add(backend, Chord.parse("Super+Y"), "spawn foot")
        assert not backend.write_target().exists()
        assert backup.list_snapshots() == []

    def test_a_refused_write_is_an_edit_error(self, fresh, monkeypatch):
        """Not a PermissionError traceback out of the CLI."""
        backend, _ = fresh

        def refuse(path, text):
            raise PermissionError(13, "Permission denied", str(path))

        monkeypatch.setattr(backup, "write_atomic", refuse)
        with pytest.raises(editor.EditError, match="cannot write"):
            editor.add(backend, Chord.parse("Super+Y"), "spawn firefox")
        assert backup.list_snapshots() == []


class TestHyprlandLuaRefusal:
    @pytest.fixture
    def lua_rw(self, tmp_path):
        root = tmp_path / "hypr"
        shutil.copytree(FIXTURES / "hyprland-lua", root)
        return HyprlandBackend(config_root=root)

    def test_add_is_refused_naming_the_lua_file(self, lua_rw):
        """Writing the .conf Hyprland ignores would report a success that
        does nothing."""
        conf = lua_rw._root / "hyprland.conf"
        before = conf.read_text()
        with pytest.raises(editor.EditError, match="hyprland.lua"):
            editor.add(lua_rw, Chord.parse("Super+Y"), "exec foot")
        assert conf.read_text() == before
        assert backup.list_snapshots() == []

    def test_an_edit_of_a_stale_record_is_refused(self, lua_rw):
        """A shortcut read before the Lua config appeared must not be written."""
        conf = lua_rw._root / "hyprland.conf"
        stale = lua_rw.parse(conf.read_text(), conf)[0]
        with pytest.raises(editor.EditError, match="Lua"):
            editor.delete(lua_rw, stale)


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

    def test_moving_a_default_to_a_new_chord_releases_the_old_one(self, cosmic_rw):
        """Otherwise both chords close windows: COSMIC keeps the default live
        at Super+Q beside the override at Super+Shift+Q."""
        target = by_chord(cosmic_rw.read())["super+q"]
        editor.rebind(cosmic_rw, target, Chord.parse("Super+Shift+Q"))
        after = by_chord(cosmic_rw.read())
        assert "super+q" not in after
        assert after["super+shift+q"].action == "Close"

    def test_retargeting_a_default_in_place_disables_nothing(self, cosmic_rw):
        disables = cosmic_rw._custom.read_text().count("Disable")
        target = by_chord(cosmic_rw.read())["super+return"]
        editor.retarget(cosmic_rw, target, "foot")
        assert cosmic_rw._custom.read_text().count("Disable") == disables
        assert by_chord(cosmic_rw.read())["super+return"].action == 'Spawn("foot")'

    def test_moving_an_override_keeps_the_shadowed_default_off(self, cosmic_rw):
        """The fixture's custom Super+T (alacritty) overrides the default
        ToggleTiling. Moving it must not bring ToggleTiling back on Super+T."""
        override = by_chord(cosmic_rw.read())["super+t"]
        assert not override.extras["readonly"]
        editor.rebind(cosmic_rw, override, Chord.parse("Super+Shift+Return"))
        after = by_chord(cosmic_rw.read())
        assert after["super+shift+return"].action == 'Spawn("alacritty")'
        assert "super+t" not in after
        assert '(modifiers: [Super], key: "t"): Disable,' in cosmic_rw._custom.read_text()

    def test_moving_a_plain_custom_binding_disables_nothing(self, cosmic_rw):
        custom = cosmic_rw._custom
        custom.write_text(
            custom.read_text().replace(
                "}", '    (modifiers: [Super], key: "y"): Spawn("foot"),\n}'
            )
        )
        editor.rebind(cosmic_rw, by_chord(cosmic_rw.read())["super+y"], Chord.parse("Super+U"))
        assert 'key: "y"): Disable' not in custom.read_text()

    def test_a_stale_default_is_not_overridden(self, cosmic_rw):
        """A custom binding put on the default's chord since it was read
        would lose to the Disable appended after it."""
        stale = by_chord(cosmic_rw.read())["super+q"]
        assert stale.extras["readonly"]
        custom = cosmic_rw.write_target()
        custom.write_text(
            custom.read_text().replace(
                "}", '    (modifiers: [Super], key: "q"): Spawn("mine"),\n}'
            )
        )
        before = custom.read_text()
        with pytest.raises(editor.EditError, match="changed since it was read"):
            editor.rebind(cosmic_rw, stale, Chord.parse("Super+Z"))
        with pytest.raises(editor.EditError, match="changed since it was read"):
            editor.delete(cosmic_rw, stale)
        assert custom.read_text() == before
        assert by_chord(cosmic_rw.read())["super+q"].action == 'Spawn("mine")'
        assert backup.list_snapshots() == []

    def test_adding_after_an_entry_without_a_trailing_comma(self, cosmic_rw):
        """RON's trailing comma is optional, but the one between entries is
        not. The lenient parser accepts the broken map, so validation alone
        would not catch it."""
        custom = cosmic_rw._custom
        custom.write_text(custom.read_text().replace("Disable,\n}", "Disable\n}"))
        editor.add(cosmic_rw, Chord.parse("Super+Y"), "foot")
        text = custom.read_text()
        assert '(modifiers: [Super], key: "w"): Disable,\n' in text
        assert "super+y" in by_chord(cosmic_rw.read())

    def test_the_comma_goes_before_a_trailing_comment(self, cosmic_rw):
        custom = cosmic_rw._custom
        custom.write_text(
            custom.read_text().replace("Disable,\n}", "Disable // gone\n}")
        )
        editor.add(cosmic_rw, Chord.parse("Super+Y"), "foot")
        assert 'key: "w"): Disable,' in custom.read_text()

    def test_a_brace_in_a_comment_around_the_map_is_not_its_end(self, cosmic_rw):
        """The closing brace is matched, not taken as the last `}` in the
        file: a comment after (or before) the map may hold one."""
        custom = cosmic_rw._custom
        custom.write_text(
            "// shortcuts {see docs}\n"
            "{\n"
            '    (modifiers: [Super], key: "b"): Spawn("firefox"),\n'
            "}\n"
            "// see {} docs\n"
        )
        editor.add(cosmic_rw, Chord.parse("Super+Y"), "foot")
        text = custom.read_text()
        assert text.endswith('Spawn("foot"),\n}\n// see {} docs\n')
        found = by_chord(cosmic_rw.read())
        assert found["super+y"].action == 'Spawn("foot")'
        assert found["super+b"].action == 'Spawn("firefox")'

    def test_repeated_adds_do_not_stack_blank_lines(self, cosmic_rw):
        editor.add(cosmic_rw, Chord.parse("Super+Y"), "foot")
        editor.add(cosmic_rw, Chord.parse("Super+U"), "kitty")
        assert cosmic_rw._custom.read_text().endswith('Spawn("kitty"),\n}\n')

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

    def test_undo_removes_a_file_the_edit_created(self, cosmic_rw):
        """A fresh COSMIC has no `custom` file until the first edit makes one."""
        custom = cosmic_rw.write_target()
        custom.unlink()
        editor.add(cosmic_rw, Chord.parse("Super+Y"), "foot")
        assert custom.exists()
        assert editor.undo_last() == [custom]
        assert not custom.exists()

    def test_a_rejected_edit_to_a_new_file_leaves_nothing_behind(
        self, cosmic_rw, monkeypatch
    ):
        custom = cosmic_rw.write_target()
        custom.unlink()
        # Render something that parses but is not the requested binding.
        monkeypatch.setattr(
            cosmic_rw, "render", lambda *a, **k: '(modifiers: [Super], key: "z"): Close,'
        )
        with pytest.raises(editor.EditError, match="did not take effect"):
            editor.add(cosmic_rw, Chord.parse("Super+Y"), "foot")
        assert not custom.exists()

    def test_a_second_undo_walks_further_back(self, mango_rw):
        path = mango_rw.config_paths()[0]
        before = path.read_text()
        editor.add(mango_rw, Chord.parse("Super+Y"), "spawn firefox")
        editor.add(mango_rw, Chord.parse("Super+U"), "spawn foot")
        editor.undo_last()
        editor.undo_last()
        assert path.read_text() == before

    def test_undo_after_a_failed_write_reverts_the_last_real_edit(
        self, niri_rw, monkeypatch
    ):
        """A rolled-back write changed nothing, so it must not be what undo
        restores."""
        path = niri_rw.config_paths()[0]
        before = path.read_text()
        editor.add(niri_rw, Chord.parse("Super+Y"), 'spawn "firefox"')
        target = by_chord(niri_rw.read())["super+b"]
        monkeypatch.setattr(niri_rw, "render", lambda *a, **k: 'Mod+Z { spawn "wrong"; }')
        with pytest.raises(editor.EditError, match="did not take effect"):
            editor.rebind(niri_rw, target, Chord.parse("Super+Shift+B"))
        editor.undo_last()
        assert path.read_text() == before

    def test_a_symlinked_config_stays_a_symlink(self, tmp_path):
        """Written through to the dotfile it points at, as restore does."""
        dotfiles = tmp_path / "dotfiles"
        shutil.copytree(FIXTURES / "mango", dotfiles)
        root = tmp_path / "mango"
        root.mkdir()
        for name in ("config.conf", "bind.conf"):
            (root / name).symlink_to(dotfiles / name)
        backend = MangoBackend(config_root=root)
        before = (dotfiles / "config.conf").read_text()

        editor.add(backend, Chord.parse("Super+Y"), "spawn firefox")
        assert (root / "config.conf").is_symlink()
        assert "bind=SUPER,y,spawn,firefox" in (dotfiles / "config.conf").read_text()

        editor.undo_last()
        assert (root / "config.conf").is_symlink()
        assert (dotfiles / "config.conf").read_text() == before

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

    def test_hyprland_wraps_in_exec(self, hypr_rw):
        assert editor.wrap_command_as_action(hypr_rw, "firefox") == "exec firefox"

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

    def test_hyprland_round_trips(self, hypr_rw):
        action = editor.wrap_command_as_action(hypr_rw, "firefox")
        assert editor.unwrap_action(hypr_rw, action) == "firefox"
        assert editor.unwrap_action(hypr_rw, "killactive") is None

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

    def test_a_failed_second_write_is_one_undo_away_from_the_start(self, niri_rw):
        """The error text tells the user to run undo; that has to restore the
        victim, not just revert a no-op."""
        path = niri_rw.config_paths()[0]
        before = path.read_text()
        victim = by_chord(niri_rw.read())["super+b"]
        ghost = by_chord(niri_rw.read())["super+e"]
        ghost.action = "spawn \"something-that-is-not-in-the-file\""
        with pytest.raises(editor.EditError):
            editor.take_over(niri_rw, victim, ghost, Chord.parse("Super+B"), "x")
        editor.undo_last()
        assert path.read_text() == before

    def test_one_undo_reverts_both_writes_across_files(self, mango_rw):
        """Victim in config.conf, target in the sourced bind.conf."""
        files = {p: p.read_text() for p in mango_rw.config_paths()}
        shortcuts = by_chord(mango_rw.read())
        victim, target = shortcuts["super+b"], shortcuts["super+g"]
        editor.take_over(mango_rw, victim, target, victim.chord, target.action)
        assert by_chord(mango_rw.read())["super+b"].action == "spawn gimp"

        editor.undo_last()
        assert {p: p.read_text() for p in files} == files

    def test_a_failed_delete_leaves_no_snapshot_behind(self, niri_rw):
        victim = by_chord(niri_rw.read())["super+b"]
        victim.source.path.write_text("// clobbered\n" + victim.source.path.read_text())
        with pytest.raises(editor.EditError, match="changed since it was read"):
            editor.take_over(niri_rw, victim, None, victim.chord, 'spawn "x"')
        assert backup.list_snapshots() == []


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

    def test_undo_removes_a_file_the_write_created(self, tmp_path):
        path = tmp_path / "new" / "rules"
        editor.write_file(path, "hello\n", "test", lambda text: True)
        assert editor.undo_last() == [path]
        assert not path.exists()

    def test_the_write_is_undoable(self, tmp_path):
        path = tmp_path / "rules"
        path.write_text("original\n")
        editor.write_file(path, "changed\n", "test", lambda text: True)
        assert path.read_text() == "changed\n"
        editor.undo_last()
        assert path.read_text() == "original\n"
