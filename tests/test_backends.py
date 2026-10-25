"""Parser tests against realistic configs for every supported compositor."""

import shutil
from pathlib import Path

import pytest

from cachy_shortcuts.model import Category, Chord

from .conftest import by_chord


class TestNiriReader:
    def test_follows_includes(self, niri):
        paths = [p.name for p in niri.config_paths()]
        assert "keybinds.kdl" in paths
        assert "config.kdl" in paths

    def test_missing_optional_include_is_not_fatal(self, niri):
        # cfg/missing.kdl is declared optional=true and does not exist.
        assert all(p.exists() for p in niri.config_paths())

    def test_file_with_binds_is_ordered_first(self, niri):
        # New bindings should land beside existing ones.
        assert niri.config_paths()[0].name == "keybinds.kdl"

    def test_reads_all_bindings(self, niri):
        found = by_chord(niri.read())
        assert "super+return" in found
        assert "super+shift+slash" in found
        assert "xf86audioraisevolume" in found

    def test_hotkey_overlay_title_becomes_description(self, niri):
        found = by_chord(niri.read())
        assert found["super+return"].description == "Open Terminal: Alacritty"

    def test_null_title_is_not_treated_as_a_description(self, niri):
        found = by_chord(niri.read())
        assert found["super+space"].description == ""

    def test_multiline_body_is_collapsed(self, niri):
        found = by_chord(niri.read())
        assert found["super+shift+f"].action == "toggle-window-floating"

    def test_properties_are_captured(self, niri):
        found = by_chord(niri.read())
        assert found["xf86audioraisevolume"].extras["props"]["allow-when-locked"] == "true"
        assert found["super+wheelscrolldown"].extras["props"]["cooldown-ms"] == "150"

    def test_slashdash_binding_is_marked_disabled(self, niri):
        found = by_chord(niri.read())
        assert found["super+shift+p"].extras["disabled"] is True

    def test_quoted_args_survive(self, niri):
        found = by_chord(niri.read())
        assert '"5%+"' in found["xf86audioraisevolume"].action

    def test_spans_point_at_the_real_text(self, niri):
        for shortcut in niri.read():
            text = shortcut.source.path.read_text()
            assert text[shortcut.source.start : shortcut.source.end] == shortcut.raw

    def test_noctalia_binds_are_attributed(self, niri):
        found = by_chord(niri.read())
        assert found["super+s"].owner == "Noctalia"


class TestMangoReader:
    def test_follows_source_directive(self, mango):
        assert "bind.conf" in [p.name for p in mango.config_paths()]

    def test_reads_bindings_from_both_files(self, mango):
        found = by_chord(mango.read())
        assert "super+return" in found
        assert "super+g" in found  # from bind.conf

    def test_none_modifier_yields_bare_chord(self, mango):
        found = by_chord(mango.read())
        assert found["xf86audioraisevolume"].chord.mods == ()

    def test_args_containing_spaces_are_preserved(self, mango):
        found = by_chord(mango.read())
        assert found["xf86monbrightnessup"].action == "spawn brightnessctl set +5%"

    def test_shell_command_with_pipe_survives(self, mango):
        found = by_chord(mango.read())
        assert '|' in found["super+ctrl+s"].action

    def test_raw_keycodes_stay_opaque(self, mango):
        # Without an xkb keymap these cannot be resolved to names, so they stay
        # verbatim. Unknown modifiers sort alphabetically after known ones,
        # which keeps the canonical form deterministic.
        found = by_chord(mango.read())
        assert "code:133+code:64+code:24" in found

    def test_tilde_in_source_directive_expands(self, tmp_path, monkeypatch):
        from cachy_shortcuts.backends import MangoBackend

        home = tmp_path / "home"
        conf_dir = home / ".config" / "mango"
        conf_dir.mkdir(parents=True)
        (conf_dir / "config.conf").write_text(
            "source=~/.config/mango/extra.conf\nbind=SUPER,a,spawn,a\n"
        )
        (conf_dir / "extra.conf").write_text("bind=SUPER,z,spawn,zed\n")
        monkeypatch.setenv("HOME", str(home))

        backend = MangoBackend(config_root=conf_dir)
        assert "extra.conf" in [p.name for p in backend.config_paths()]
        assert "super+z" in by_chord(backend.read())

    def test_spaced_fields_come_back_clean(self, mango):
        """`bind = SUPER+ALT, Up, focusmon, up` is one action, not
        "focusmon  up" with the separator's space still in it."""
        found = by_chord(mango.read())
        assert found["super+alt+up"].action == "focusmon up"
        assert found["super+r"].action == "reload_config"

    def test_bind_flags_are_captured(self, mango):
        found = by_chord(mango.read())
        assert found["super+l"].extras["flags"] == "l"

    def test_keymode_bindings_are_tagged(self, mango):
        scoped = {s.chord.canonical: s for s in mango.read() if s.extras["submap"]}
        assert set(scoped) == {"h", "l", "super+q"}
        assert all(s.extras["keymode"] == "resize" for s in scoped.values())

    def test_keymode_default_ends_the_scope(self, mango):
        """The fixture closes its resize block with `keymode=default`."""
        text = mango.config_paths()[0].read_text() + "bind=SUPER,F9,spawn,foot\n"
        parsed = mango.parse(text, mango.config_paths()[0])
        assert next(s for s in parsed if s.chord.canonical == "super+f9").extras["submap"] == ""

    def test_inline_comments_are_cut_as_mango_cuts_them(self, mango):
        """remove_comment(): a `#` after whitespace, outside quotes, starts a
        comment -- on keymode, bind and source lines alike."""
        text = (
            "keymode=resize\n"
            "bind=NONE,Escape,setkeymode,default\n"
            "keymode=default   # back to normal binds\n"
            "bind=SUPER,x,spawn,foot # terminal\n"
            'bind=SUPER,y,spawn,notify-send "a # b"#tail\n'
        )
        parsed = by_chord(mango.parse(text, Path("config.conf")))
        x, y = parsed["super+x"], parsed["super+y"]
        assert x.extras["keymode"] == "default" and x.extras["submap"] == ""
        assert x.action == "spawn foot"
        assert x.raw == "bind=SUPER,x,spawn,foot"
        assert text[x.source.start : x.source.end] == x.raw
        assert y.action == 'spawn notify-send "a # b"#tail'

    def test_a_source_line_with_a_comment_is_followed(self, tmp_path):
        from cachy_shortcuts.backends import MangoBackend

        (tmp_path / "config.conf").write_text("source=./more.conf  # extras\n")
        (tmp_path / "more.conf").write_text("bind=SUPER,x,spawn,foot\n")
        backend = MangoBackend(config_root=tmp_path, system_config=tmp_path / "none")
        assert "super+x" in by_chord(backend.read())

    def test_a_keymode_carries_into_a_sourced_file_and_out_again(self, tmp_path):
        """The keymode is one parser state: parse_config_file() never resets
        it, and `source` is read inline."""
        from cachy_shortcuts.backends import MangoBackend

        (tmp_path / "config.conf").write_text(
            "bind=SUPER,F,setkeymode,resize\n"
            "keymode=resize\n"
            "source=./dms/binds.conf\n"
            "bind=NONE,Left,resizewin,-10,0\n"
            "source=./leaves-open.conf\n"
            "bind=SUPER,x,spawn,foot\n"
        )
        (tmp_path / "dms").mkdir()
        (tmp_path / "dms" / "binds.conf").write_text("bind=SUPER,Space,spawn,dms ipc call spotlight toggle\n")
        (tmp_path / "leaves-open.conf").write_text("keymode=default\nbind=SUPER,y,spawn,kitty\n")
        backend = MangoBackend(config_root=tmp_path, system_config=tmp_path / "none")
        shortcuts = backend.read()
        scope = {s.chord.canonical: s.extras["submap"] for s in shortcuts}
        assert scope == {
            "super+f": "",
            "super+space": "resize",
            "left": "resize",
            "super+y": "",
            "super+x": "",
        }
        # In the order mango loads them, not file by file.
        assert [s.chord.canonical for s in shortcuts] == list(scope)

    def test_source_optional_and_the_c_flag_are_read(self, mango):
        """Both were skipped, so a taken chord could be offered as free."""
        assert "extra.conf" in [p.name for p in mango.config_paths()]
        assert by_chord(mango.read())["super+o"].extras["flags"] == "c"

    def test_a_missing_optional_source_is_not_fatal(self, mango):
        assert "not-there.conf" not in [p.name for p in mango.config_paths()]

    def test_comments_are_skipped(self, mango):
        assert all(not s.raw.startswith("#") for s in mango.read())

    def test_spans_point_at_the_real_text(self, mango):
        for shortcut in mango.read():
            text = shortcut.source.path.read_text()
            assert text[shortcut.source.start : shortcut.source.end] == shortcut.raw

    def test_dms_binds_are_attributed(self, mango):
        found = by_chord(mango.read())
        assert found["super+space"].owner == "DMS"


class TestHyprlandReader:
    def test_follows_source_directive(self, hyprland):
        assert "binds.conf" in [p.name for p in hyprland.config_paths()]

    def test_variables_expand_in_chords_and_actions(self, hyprland):
        found = by_chord(hyprland.read())
        # $mainMod -> SUPER, $terminal -> alacritty
        assert found["super+return"].action == "exec alacritty"

    def test_variables_reach_sourced_files(self, hyprland):
        # binds.conf uses $mainMod, which is only defined in hyprland.conf.
        found = by_chord(hyprland.read())
        assert found["super+p"].action == "exec hyprshot -m region"

    def test_bindd_description_is_read(self, hyprland):
        found = by_chord(hyprland.read())
        assert found["super+b"].description == "Web browser"

    def test_run_together_modifiers_are_split(self, hyprland):
        # `SUPERSHIFT` is the same chord as `SUPER SHIFT`.
        found = by_chord(hyprland.read())
        assert found["super+shift+f"].chord.mods == ("super", "shift")

    def test_empty_modifier_field_yields_bare_chord(self, hyprland):
        found = by_chord(hyprland.read())
        assert found["xf86audiomute"].chord.mods == ()

    def test_bind_flags_are_captured(self, hyprland):
        found = by_chord(hyprland.read())
        assert found["super+l"].extras["flags"] == "l"
        assert found["super+mouse:272"].extras["flags"] == "m"

    def test_an_unbind_in_a_later_file_disables_the_earlier_bind(self, hyprland):
        """overrides.conf, sourced last, unbinds Super+T and rebinds it."""
        from cachy_shortcuts import conflicts

        shortcuts = hyprland.read()
        on_t = [s for s in shortcuts if s.chord == Chord.parse("Super+T")]
        assert [(s.action, bool(s.extras.get("disabled"))) for s in on_t] == [
            ("togglefloating", True),
            ("exec alacritty", False),
        ]
        assert conflicts.find_conflicts(shortcuts) == []
        claimed = conflicts.claimant(Chord.parse("Super+T"), shortcuts)
        assert claimed.action == "exec alacritty"

    def test_sourced_binds_sit_where_their_source_line_is(self, hyprland):
        order = [s.chord.canonical for s in hyprland.read()]
        # `source = binds.conf` comes before the Applications section.
        assert order.index("super+p") < order.index("super+return")

    def test_unbind_reaches_earlier_binds_in_the_same_file(self, hyprland):
        text = (
            "bind = SUPER, Q, killactive,\n"
            "submap = resize\nbind = SUPER, Q, submap, reset\nsubmap = reset\n"
            "unbind = SUPER, Q\n"
            "bind = SUPER, Q, exec, foot\n"
        )
        parsed = hyprland.parse(text, Path("hyprland.conf"))
        assert [bool(s.extras.get("disabled")) for s in parsed] == [True, True, False]

    def test_unbind_all_clears_everything_above_it(self, hyprland):
        text = "bind = SUPER, Q, killactive,\nunbind = all\nbind = SUPER, W, exec, foot\n"
        parsed = hyprland.parse(text, Path("hyprland.conf"))
        assert [bool(s.extras.get("disabled")) for s in parsed] == [True, False]

    def test_unbind_compares_the_key_string_exactly(self, hyprland):
        """removeKeybind compares the key string, not the key: an unbind of
        `t` leaves a bind on `T` live in Hyprland."""
        text = (
            "bind = SUPER, T, togglefloating,\n"
            "bind = SUPER, Q, killactive,\n"
            "unbind = SUPER, t\n"
            "unbind = SUPER_SHIFT, Q\n"
        )
        parsed = hyprland.parse(text, Path("hyprland.conf"))
        assert [bool(s.extras.get("disabled")) for s in parsed] == [False, False]

    def test_unbind_matches_the_modmask_and_the_keycode_forms(self, hyprland):
        """The modmask is a set of bits, however the names are spelt; `code:N`
        and a bare number above 9 are the same keycode (parseKey)."""
        text = (
            "bind = SUPER SHIFT, Q, killactive,\n"
            "bind = SUPER, code:10, workspace, 1\n"
            "unbind = SHIFT+WIN, Q\n"
            "unbind = SUPER, 10\n"
        )
        parsed = hyprland.parse(text, Path("hyprland.conf"))
        assert [bool(s.extras.get("disabled")) for s in parsed] == [True, True]

    def test_a_universal_bind_in_a_submap_competes_with_global_chords(self, hyprland):
        """`bindu` fires in every submap, the global one included, so it is
        scoped as global wherever it is written."""
        from cachy_shortcuts import conflicts

        text = (
            "bind = SUPER, Q, killactive,\n"
            "submap = resize\n"
            "bindu = SUPER, Q, submap, reset\n"
            "bind = , escape, submap, reset\n"
            "submap = reset\n"
        )
        parsed = hyprland.parse(text, Path("hyprland.conf"))
        assert [(s.extras["submap"], s.extras["block"]) for s in parsed] == [
            ("", ""),
            ("", "resize"),
            ("resize", "resize"),
        ]
        (conflict,) = conflicts.find_conflicts(parsed)
        assert conflict.chord == Chord.parse("Super+Q")

    def test_inline_comments_end_submap_bind_and_source_lines(self, hyprland):
        """hyprlang starts a comment at any `#`; `##` is a literal one."""
        text = (
            "submap = resize\n"
            "bind = , right, resizeactive, 10 0\n"
            "submap = reset   # back to global\n"
            "bind = SUPER, X, exec, foot # terminal\n"
            "bind = SUPER, Y, exec, echo ##1 # note\n"
        )
        parsed = by_chord(hyprland.parse(text, Path("hyprland.conf")))
        x = parsed["super+x"]
        assert x.extras["submap"] == ""
        assert x.action == "exec foot"
        assert x.raw == "bind = SUPER, X, exec, foot"
        assert text[x.source.start : x.source.end] == x.raw
        assert parsed["super+y"].raw == "bind = SUPER, Y, exec, echo ##1"

    def test_a_submap_line_names_its_submap_before_the_comma(self, hyprland):
        text = "submap = resize, reset\nbind = , right, resizeactive, 10 0\nsubmap = reset\n"
        (bind,) = hyprland.parse(text, Path("hyprland.conf"))
        assert bind.extras["submap"] == "resize"

    def test_a_submap_carries_into_a_sourced_file_and_out_again(self, tmp_path):
        """m_currentSubmap survives handleSource: a file sourced inside a
        submap block binds into that submap."""
        from cachy_shortcuts.backends import HyprlandBackend

        (tmp_path / "hyprland.conf").write_text(
            "bind = SUPER, R, submap, resize\n"
            "submap = resize\n"
            "source = resize.conf\n"
            "submap = reset\n"
            "source = opens.conf\n"
            "bind = SUPER, X, exec, foot\n"
        )
        (tmp_path / "resize.conf").write_text("bind = , escape, submap, reset\n")
        (tmp_path / "opens.conf").write_text("submap = launch\nbind = , f, exec, firefox\n")
        backend = HyprlandBackend(config_root=tmp_path)
        scope = {s.chord.canonical: s.extras["submap"] for s in backend.read()}
        assert scope == {"super+r": "", "escape": "resize", "f": "launch", "super+x": "launch"}

    def test_a_commented_source_line_is_followed(self, tmp_path):
        from cachy_shortcuts.backends import HyprlandBackend

        (tmp_path / "hyprland.conf").write_text("source = more.conf # extras\n")
        (tmp_path / "more.conf").write_text("bind = SUPER, X, exec, foot\n")
        backend = HyprlandBackend(config_root=tmp_path)
        assert "super+x" in by_chord(backend.read())

    def test_newer_bind_flags_are_read(self, hyprland):
        """`bindu` (and a, g, x) were skipped by the older flag list."""
        found = by_chord(hyprland.read())
        assert found["super+escape"].extras["flags"] == "u"
        assert found["super+escape"].action == "submap reset"

    def test_raw_keycodes_stay_opaque(self, hyprland):
        found = by_chord(hyprland.read())
        assert "code:133+code:24" in found

    def test_submap_bindings_are_tagged(self, hyprland):
        scoped = {s.chord.canonical for s in hyprland.read() if s.extras["submap"]}
        assert "right" in scoped
        assert all(s.extras["submap"] == "" for s in hyprland.read() if s.chord.canonical == "super+1")

    def test_submap_reset_ends_the_scope(self, hyprland):
        # binds.conf is read after the submap block closes.
        found = by_chord(hyprland.read())
        assert found["super+p"].extras["submap"] == ""

    def test_comments_are_skipped(self, hyprland):
        assert all(not s.raw.startswith("#") for s in hyprland.read())

    def test_spans_point_at_the_real_text(self, hyprland):
        for shortcut in hyprland.read():
            text = shortcut.source.path.read_text()
            assert text[shortcut.source.start : shortcut.source.end] == shortcut.raw

    def test_keypad_enter_is_not_a_second_return(self, hyprland):
        """binds.conf binds KP_Enter to the same terminal as Return."""
        from cachy_shortcuts import conflicts

        found = by_chord(hyprland.read())
        assert found["super+kp_enter"].action == found["super+return"].action
        assert conflicts.find_conflicts(hyprland.read()) == []

    def test_noctalia_binds_are_attributed(self, hyprland):
        found = by_chord(hyprland.read())
        assert found["super+space"].owner == "Noctalia"

    def test_noctalia_5_msg_cli_is_attributed(self):
        # Noctalia 5.1 and the CachyOS hypr-noctalia profile call
        # `noctalia msg ...` rather than going through quickshell.
        from cachy_shortcuts.model import Shortcut

        bind = Shortcut(
            chord=Chord.parse("Super+Space"),
            action="exec noctalia msg panel-toggle launcher",
        )
        assert bind.owner == "Noctalia"

    def test_a_config_without_a_shell_still_reads(self, hyprland_vanilla):
        found = by_chord(hyprland_vanilla.read())
        assert found["super+return"].action == "exec kitty"
        assert all(s.owner is None for s in hyprland_vanilla.read())

    def test_a_config_without_variables_renders_unchanged(self, hyprland_vanilla):
        for shortcut in hyprland_vanilla.read():
            rendered = hyprland_vanilla.render(
                shortcut.chord, shortcut.action, shortcut.description, shortcut.extras
            )
            assert rendered == shortcut.raw

    def test_the_same_chord_is_one_identity_with_or_without_a_shell(
        self, hyprland, hyprland_vanilla
    ):
        shell = by_chord(hyprland.read())
        bare = by_chord(hyprland_vanilla.read())
        for canonical in ("super+return", "super+b", "super+1"):
            assert shell[canonical].chord == bare[canonical].chord


class TestHyprlandLuaConfig:
    """Hyprland 0.55+ loads hyprland.lua over hyprland.conf, so the Lua config
    is what is read -- and the ignored .conf beside it must not stand in."""

    def test_the_lua_file_is_what_hyprland_loads(self, hyprland_lua):
        assert hyprland_lua.main_config().name == "hyprland.lua"
        assert hyprland_lua.lua_config() == hyprland_lua.main_config()

    def test_the_lua_files_are_listed_with_the_binds_file_first(self, hyprland_lua):
        names = [p.name for p in hyprland_lua.config_paths()]
        assert names[0] == "binds.lua"  # where new binds belong
        assert set(names) == {"binds.lua", "hyprland.lua", "variables.lua"}
        assert "hyprland.conf" not in names

    def test_the_ignored_conf_is_not_read(self, hyprland_lua):
        found = by_chord(hyprland_lua.read())
        # the stale conf binds Super+Q to killactive; the Lua one to window.close()
        assert found["super+q"].action == "window.close()"
        assert all("killactive" not in s.action for s in hyprland_lua.read())

    def test_nothing_is_refused(self, hyprland_lua):
        assert hyprland_lua.unsupported() is None

    def test_a_conf_only_config_is_unaffected(self, hyprland):
        assert hyprland.unsupported() is None
        assert hyprland.main_config().name == "hyprland.conf"

    def test_hyprland_config_env_is_honoured(self, tmp_path, monkeypatch):
        from cachy_shortcuts.backends import HyprlandBackend

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        explicit = tmp_path / "elsewhere" / "main.conf"
        explicit.parent.mkdir()
        explicit.write_text("bind = SUPER, Q, killactive,\n")
        monkeypatch.setenv("HYPRLAND_CONFIG", str(explicit))
        backend = HyprlandBackend()
        assert backend.config_paths() == [explicit]
        assert "super+q" in by_chord(backend.read())

    def test_hyprland_config_env_pointing_at_lua_reads_that_lua(
        self, tmp_path, monkeypatch
    ):
        from cachy_shortcuts.backends import HyprlandBackend

        main = tmp_path / "main.lua"
        main.write_text('hl.bind("SUPER + Q", hl.dsp.window.close())\n')
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("HYPRLAND_CONFIG", str(main))
        backend = HyprlandBackend()
        assert backend.lua_config() == main
        assert "super+q" in by_chord(backend.read())


class TestHyprlandLuaParse:
    """The scanner alone: what one file says, with no lua to evaluate it."""

    @pytest.fixture
    def parsed(self, hyprland_lua):
        path = next(p for p in hyprland_lua.config_paths() if p.name == "binds.lua")
        return by_chord(hyprland_lua.parse(path.read_text(), path)), path

    def test_mod_concatenation_folds_to_a_chord(self, parsed):
        found, _ = parsed
        assert found["super+q"].action == "window.close()"
        assert found["alt+tab"].action == "window.cycle_next()"

    def test_constants_from_another_file_fold(self, parsed):
        found, _ = parsed
        # TERMINAL is defined in config/variables.lua, launchPrefix in this file
        assert found["super+return"].action == "exec uwsm app -- kitty"

    def test_exec_cmd_reads_as_the_exec_action_every_backend_uses(self, parsed):
        found, _ = parsed
        assert found["super+space"].action == "exec noctalia msg panel-toggle launcher"
        assert found["super+space"].owner == "Noctalia"

    def test_other_dispatchers_keep_their_source_minus_the_namespace(self, parsed):
        found, _ = parsed
        assert found["super+alt+space"].action == 'window.float({ action = "toggle" })'

    def test_the_description_option_is_the_description(self, parsed):
        found, _ = parsed
        assert found["super+w"].description == "Browser"

    def test_bind_options_become_flags(self, parsed):
        found, _ = parsed
        assert found["xf86audioraisevolume"].extras["flags"] == ["locked", "repeating"]

    def test_mouse_and_keycode_spellings_normalise(self, parsed):
        found, _ = parsed
        assert "super+mouse:272" in found
        assert "super+mouse_down" in found
        assert "super+code:82" in found

    def test_an_inline_function_is_a_lua_function_action(self, parsed):
        found, _ = parsed
        assert found["super+minus"].action == "lua function"

    def test_comments_and_long_strings_hold_no_binds(self, parsed):
        found, _ = parsed
        assert "super+z" not in found
        assert "super+y" not in found

    def test_the_source_span_is_exactly_the_call(self, parsed):
        found, path = parsed
        q = found["super+q"]
        text = path.read_text()
        assert text[q.source.start : q.source.end] == q.raw
        assert q.raw == 'hl.bind(mainMod .. " + Q",           hl.dsp.window.close())'

    def test_submap_binds_are_scoped_to_their_mode(self, parsed):
        found, _ = parsed
        assert found["h"].extras["submap"] == "resize"
        assert found["escape"].extras["submap"] == "resize"
        assert found["super+r"].extras["submap"] == ""

    def test_an_unbind_disables_the_earlier_bind(self, parsed):
        found, _ = parsed
        assert found["super+b"].extras["disabled"] is True

    def test_binds_made_by_loops_and_helpers_are_not_editable_spans(self, parsed):
        found, _ = parsed
        assert "super+alt+code:10" not in found
        assert "super+shift+code:10" not in found


class TestHyprlandLuaEvaluation:
    """With lua on the machine the config is run, which is the only way to see
    the binds a loop or helper function makes."""

    needs_lua = pytest.mark.skipif(shutil.which("lua") is None, reason="no lua")

    @needs_lua
    def test_loop_and_helper_binds_are_listed(self, hyprland_lua):
        found = by_chord(hyprland_lua.read())
        assert found["super+shift+code:10"].action == 'window.move({ monitor = "HDMI-A-1" })'
        assert found["super+alt+code:10"].action == "focus({ workspace = 1 })"
        assert found["super+alt+code:11"].action == "focus({ workspace = 2 })"

    @needs_lua
    def test_generated_binds_have_no_span_and_say_why(self, hyprland_lua):
        found = by_chord(hyprland_lua.read())
        gen = found["super+alt+code:11"]
        assert gen.source is None
        assert gen.extras["generated"] is True
        assert "binds.lua" in gen.extras["readonly_reason"]

    @needs_lua
    def test_a_scanner_bind_keeps_its_span_when_evaluated(self, hyprland_lua):
        q = by_chord(hyprland_lua.read())["super+q"]
        assert q.source is not None
        assert q.raw.startswith("hl.bind(")

    @needs_lua
    def test_every_bind_appears_once(self, hyprland_lua):
        chords = [(s.chord.canonical, s.extras.get("submap")) for s in hyprland_lua.read()]
        assert len(chords) == len(set(chords))

    @needs_lua
    def test_the_unbind_is_applied_by_evaluation_too(self, hyprland_lua):
        assert by_chord(hyprland_lua.read())["super+b"].extras["disabled"] is True

    @needs_lua
    def test_generated_binds_take_part_in_conflict_detection(self, hyprland_lua):
        from cachy_shortcuts import conflicts

        taken = conflicts.claimant(
            Chord.parse("Super+Alt+code:11"), hyprland_lua.read()
        )
        assert taken is not None

    def test_without_lua_only_what_the_scanner_can_place_is_listed(
        self, hyprland_lua, monkeypatch
    ):
        from cachy_shortcuts.backends import _lua

        monkeypatch.setattr(_lua, "evaluate", lambda *a, **k: None)
        found = by_chord(hyprland_lua.read())
        assert "super+q" in found
        assert "super+alt+code:11" not in found
        assert found["super+b"].extras["disabled"] is True
        assert all(s.source is not None for s in found.values())

    def test_a_config_that_errors_keeps_what_it_got_to(self, tmp_path):
        from cachy_shortcuts.backends import HyprlandBackend

        (tmp_path / "hyprland.lua").write_text(
            'hl.bind("SUPER + Q", hl.dsp.window.close())\n'
            "error('boom')\n"
            'hl.bind("SUPER + W", hl.dsp.exec_cmd("firefox"))\n'
        )
        found = by_chord(HyprlandBackend(config_root=tmp_path).read())
        assert {"super+q", "super+w"} <= set(found)  # the scanner covers the rest


class TestCosmicReader:
    def test_reads_defaults(self, cosmic):
        found = by_chord(cosmic.read())
        assert "super+q" in found
        assert "print" in found

    def test_custom_overrides_default(self, cosmic):
        found = by_chord(cosmic.read())
        # defaults bind Super+t to ToggleTiling; custom rebinds it to a spawn.
        assert found["super+t"].action == 'Spawn("alacritty")'

    def test_disable_removes_a_default(self, cosmic):
        found = by_chord(cosmic.read())
        assert "super+w" not in found

    def test_spawn_is_humanized(self, cosmic):
        found = by_chord(cosmic.read())
        assert found["super+b"].description == "firefox"

    def test_structured_actions_are_humanized(self, cosmic):
        found = by_chord(cosmic.read())
        assert found["super+shift+left"].description == "Move left"
        assert found["super+escape"].description == "Lock screen"

    def test_empty_modifier_list_parses(self, cosmic):
        found = by_chord(cosmic.read())
        assert found["print"].chord.mods == ()

    def test_defaults_are_marked_readonly(self, cosmic):
        found = by_chord(cosmic.read())
        assert found["super+q"].extras["readonly"] is True
        assert found["super+b"].extras["readonly"] is False

    def test_spans_point_at_the_real_text(self, cosmic):
        for path in cosmic.config_paths():
            text = path.read_text()
            for shortcut in cosmic.parse(text, path):
                assert text[shortcut.source.start : shortcut.source.end] == shortcut.raw


class TestCategorisation:
    def test_each_backend_produces_sensible_categories(self, all_backends):
        for backend in all_backends:
            cats = {s.category for s in backend.read()}
            assert Category.LAUNCH in cats, backend.name
            assert Category.WINDOWS in cats, backend.name

    def test_workspace_binds_group_together(self, all_backends):
        for backend in all_backends:
            found = by_chord(backend.read())
            assert found["super+1"].category == Category.WORKSPACES, backend.name


class TestCrossBackendAgreement:
    """The payoff: the same physical chord is one identity everywhere."""

    @pytest.mark.parametrize("canonical", ["super+return", "super+b", "super+q"])
    def test_shared_chords_have_identical_identity(self, all_backends, canonical):
        for backend in all_backends:
            assert canonical in by_chord(backend.read()), backend.name

    def test_launch_terminal_is_the_same_chord_in_all_three(self, all_backends):
        chord = Chord.parse("Super+Return")
        for backend in all_backends:
            assert chord.canonical in by_chord(backend.read()), backend.name


def _fake_subprocess(run):
    """Stands in for the ``subprocess`` name in backends.base only.

    Patching ``subprocess.run`` itself would also replace it for everything
    else that shells out, such as the Lua evaluator reading the config.
    """
    import subprocess
    import types

    return types.SimpleNamespace(run=run, SubprocessError=subprocess.SubprocessError)


class TestHyprlandRun:
    """Triggering a binding without pressing it: the hyprctl command it becomes."""

    needs_lua = pytest.mark.skipif(shutil.which("lua") is None, reason="no lua")

    @staticmethod
    def command(backend, chord):
        return backend.run_command(by_chord(backend.read())[chord])

    def test_an_exec_bind_runs_as_the_resolved_command(self, hyprland_lua):
        assert self.command(hyprland_lua, "super+return") == [
            "hyprctl",
            "eval",
            'hl.dispatch(hl.dsp.exec_cmd("uwsm app -- kitty"))',
        ]

    def test_a_dispatcher_with_a_table_argument_runs_as_written(self, hyprland_lua):
        assert self.command(hyprland_lua, "super+alt+space")[2] == (
            'hl.dispatch(hl.dsp.window.float({ action = "toggle" }))'
        )

    @needs_lua
    def test_a_loop_generated_bind_runs_with_its_values_filled_in(self, hyprland_lua):
        # `focus({ workspace = i })` -- i is gone by the time the compositor sees it
        assert self.command(hyprland_lua, "super+alt+code:11")[2] == (
            "hl.dispatch(hl.dsp.focus({ workspace = 2 }))"
        )

    @needs_lua
    def test_a_helper_built_bind_uses_the_evaluated_globals(self, hyprland_lua):
        assert self.command(hyprland_lua, "super+shift+code:10")[2] == (
            'hl.dispatch(hl.dsp.window.move({ monitor = "HDMI-A-1" }))'
        )

    def test_an_inline_lua_function_cannot_be_run_and_says_so(self, hyprland_lua):
        from cachy_shortcuts.backends.base import NotRunnable

        with pytest.raises(NotRunnable, match="Lua function"):
            self.command(hyprland_lua, "super+minus")

    def test_a_mouse_drag_cannot_be_run(self, hyprland_lua):
        from cachy_shortcuts.backends.base import NotRunnable

        with pytest.raises(NotRunnable, match="mouse"):
            self.command(hyprland_lua, "super+mouse:272")

    def test_an_unbound_bind_cannot_be_run(self, hyprland_lua):
        from cachy_shortcuts.backends.base import NotRunnable

        with pytest.raises(NotRunnable, match="unbound"):
            self.command(hyprland_lua, "super+b")

    def test_a_conf_bind_runs_through_dispatch(self, hyprland_vanilla):
        found = by_chord(hyprland_vanilla.read())
        exec_bind = next(s for s in found.values() if s.action.startswith("exec "))
        command = hyprland_vanilla.run_command(exec_bind)
        assert command[:3] == ["hyprctl", "dispatch", "exec"]
        assert command[3] == exec_bind.action[len("exec ") :]
        kill = next(s for s in found.values() if s.action.strip() == "killactive")
        assert hyprland_vanilla.run_command(kill) == ["hyprctl", "dispatch", "killactive"]

    def test_a_compositor_with_no_way_in_says_so(self, niri):
        from cachy_shortcuts.backends.base import NotRunnable

        with pytest.raises(NotRunnable, match="can't be run"):
            niri.run_command(niri.read()[0])

    def test_hyprctl_refusing_the_call_is_reported(self, hyprland_lua, monkeypatch):
        import subprocess

        from cachy_shortcuts.backends import base
        from cachy_shortcuts.backends.base import NotRunnable

        seen = []

        def fake(cmd, **kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 7, "error: attempt to index a nil value\n", "")

        target = by_chord(hyprland_lua.read())["super+q"]
        monkeypatch.setattr(base, "subprocess", _fake_subprocess(fake))
        with pytest.raises(NotRunnable, match="attempt to index"):
            hyprland_lua.run(target)
        assert seen[0][:2] == ["hyprctl", "eval"]

    def test_a_clean_ok_is_not_an_error(self, hyprland_lua, monkeypatch):
        import subprocess

        from cachy_shortcuts.backends import base

        target = by_chord(hyprland_lua.read())["super+q"]
        monkeypatch.setattr(
            base,
            "subprocess",
            _fake_subprocess(lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "ok\n", "")),
        )
        hyprland_lua.run(target)
