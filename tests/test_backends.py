"""Parser tests against realistic configs for every supported compositor."""

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
    """Hyprland 0.55+ loads hyprland.lua over hyprland.conf. The Lua config is
    not readable here, so the ignored .conf must not stand in for it."""

    def test_the_lua_file_is_what_hyprland_loads(self, hyprland_lua):
        assert hyprland_lua.main_config().name == "hyprland.lua"
        assert hyprland_lua.lua_config() == hyprland_lua.main_config()

    def test_the_ignored_conf_is_not_read(self, hyprland_lua):
        assert hyprland_lua.config_paths() == []
        assert hyprland_lua.read() == []

    def test_the_reason_names_the_lua_file(self, hyprland_lua):
        assert "hyprland.lua" in hyprland_lua.unsupported()

    def test_no_float_rule_is_offered(self, hyprland_lua):
        assert hyprland_lua.float_rule() is None

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

    def test_hyprland_config_env_pointing_at_lua_is_unsupported(
        self, tmp_path, monkeypatch
    ):
        from cachy_shortcuts.backends import HyprlandBackend

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("HYPRLAND_CONFIG", str(tmp_path / "main.lua"))
        assert "main.lua" in HyprlandBackend().unsupported()


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
