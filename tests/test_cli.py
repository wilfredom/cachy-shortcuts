"""CLI, conflict detection, learning mode and app scanning."""

import json
import shutil

import pytest

from cachy_shortcuts import appscan, cli, conflicts, usage
from cachy_shortcuts.backends import NiriBackend
from cachy_shortcuts.model import Chord, Shortcut, SourceRef, describe_action

from .conftest import FIXTURES, by_chord


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A throwaway XDG environment seeded with all three fixture configs."""
    config = tmp_path / "config"
    config.mkdir()
    shutil.copytree(FIXTURES / "niri", config / "niri")
    shutil.copytree(FIXTURES / "mango", config / "mango")
    shutil.copytree(FIXTURES / "cosmic" / "config" / "cosmic", config / "cosmic")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("NIRI_SOCKET", raising=False)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "niri")
    return config


class TestListCommand:
    def test_json_output_is_valid(self, env, capsys):
        assert cli.main(["list", "--json", "--all"]) == 0
        payload = json.loads(capsys.readouterr().out)
        names = {entry["backend"] for entry in payload}
        assert names == {"niri", "cosmic", "mango"}

    def test_json_entries_carry_the_useful_fields(self, env, capsys):
        cli.main(["list", "--json", "--backend", "niri"])
        payload = json.loads(capsys.readouterr().out)
        entry = next(
            s for s in payload[0]["shortcuts"] if s["chord"] == "super+return"
        )
        assert entry["display"] == "SUPER + RETURN"
        assert entry["description"] == "Open Terminal: Alacritty"
        assert entry["category"] == "Launch"
        assert entry["source"].endswith("keybinds.kdl:4")

    def test_query_filters_results(self, env, capsys):
        cli.main(["list", "firefox", "--backend", "niri"])
        out = capsys.readouterr().out
        assert "Open Browser" in out
        assert "close-window" not in out

    def test_active_session_is_used_when_no_flag_given(self, env, capsys):
        cli.main(["list", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert [e["backend"] for e in payload] == ["niri"]

    def test_disabled_bindings_are_marked(self, env, capsys):
        cli.main(["list", "--backend", "niri"])
        assert "(disabled)" in capsys.readouterr().out


class TestMutatingCommands:
    def test_add_then_remove_round_trip(self, env, capsys):
        assert cli.main(["add", "Super+N", "obsidian", "--backend", "niri"]) == 0
        backend = NiriBackend(config_root=env / "niri")
        assert "super+n" in by_chord(backend.read())

        assert cli.main(["rm", "Super+N", "--backend", "niri"]) == 0
        assert "super+n" not in by_chord(backend.read())

    def test_add_refuses_a_claimed_chord(self, env, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["add", "Super+B", "chromium", "--backend", "niri"])
        assert excinfo.value.code == 1
        assert "already" in capsys.readouterr().err

    def test_force_takes_a_claimed_chord(self, env):
        assert cli.main(["add", "Super+B", "chromium", "--backend", "niri", "--force"]) == 0

    def test_undo_reverts_the_last_change(self, env):
        path = env / "niri" / "cfg" / "keybinds.kdl"
        before = path.read_text()
        cli.main(["add", "Super+N", "obsidian", "--backend", "niri"])
        assert path.read_text() != before
        assert cli.main(["undo"]) == 0
        assert path.read_text() == before

    def test_undo_with_nothing_to_undo(self, env, capsys):
        assert cli.main(["undo"]) == 1
        assert "Nothing to undo" in capsys.readouterr().out

    def test_bad_chord_is_rejected_clearly(self, env, capsys):
        with pytest.raises(SystemExit):
            cli.main(["add", "Hyper+Q", "foo", "--backend", "niri"])
        assert "bad chord" in capsys.readouterr().err

    def test_removing_an_unbound_chord_fails_cleanly(self, env, capsys):
        with pytest.raises(SystemExit):
            cli.main(["rm", "Super+F12", "--backend", "niri"])
        assert "not bound" in capsys.readouterr().err

    def test_restore_lists_snapshots(self, env, capsys):
        cli.main(["add", "Super+N", "obsidian", "--backend", "niri"])
        assert cli.main(["restore", "--list"]) == 0
        assert "add" in capsys.readouterr().out


class TestDoctor:
    def test_reports_every_backend(self, env, capsys):
        cli.main(["doctor"])
        out = capsys.readouterr().out
        for name in ("Niri", "COSMIC", "MangoWM"):
            assert name in out

    def test_reports_the_active_session(self, env, capsys):
        cli.main(["doctor"])
        assert "active session : niri" in capsys.readouterr().out

    def test_exit_code_flags_conflicts(self, env, capsys):
        conf = env / "mango" / "config.conf"
        conf.write_text(conf.read_text() + "\nbind=SUPER,Return,spawn,kitty\n")
        assert cli.main(["doctor"]) == 1
        assert "conflicts: 1" in capsys.readouterr().out


class TestConflictDetection:
    def _shortcut(self, chord_text, action, backend="niri", path="/tmp/x"):
        from pathlib import Path

        return Shortcut(
            chord=Chord.parse(chord_text),
            action=action,
            source=SourceRef(backend, Path(path), 0, 1, 1),
        )

    def test_duplicate_within_a_backend_is_a_conflict(self):
        found = conflicts.find_conflicts(
            [
                self._shortcut("Super+B", "spawn firefox"),
                self._shortcut("Super+B", "spawn chromium"),
            ]
        )
        assert len(found) == 1
        assert found[0].chord == Chord.parse("Super+B")

    def test_same_chord_in_different_backends_is_not_a_conflict(self):
        # The user only runs one compositor at a time; Super+Return meaning
        # "terminal" everywhere is the goal, not a collision.
        found = conflicts.find_conflicts(
            [
                self._shortcut("Super+Return", "spawn alacritty", backend="niri"),
                self._shortcut("Super+Return", "spawn alacritty", backend="mango"),
            ]
        )
        assert found == []

    def test_disabled_bindings_do_not_conflict(self):
        disabled = self._shortcut("Super+B", "spawn chromium")
        disabled.extras["disabled"] = True
        found = conflicts.find_conflicts(
            [self._shortcut("Super+B", "spawn firefox"), disabled]
        )
        assert found == []

    def test_claimant_names_the_owning_shell(self, env):
        backend = NiriBackend(config_root=env / "niri")
        message = conflicts.describe_claimant(Chord.parse("Super+S"), backend.read())
        assert message == "already: Noctalia"

    def test_first_free_skips_claimed_chords(self, env):
        backend = NiriBackend(config_root=env / "niri")
        chosen = conflicts.first_free(
            [Chord.parse("Super+B"), Chord.parse("Super+Q"), Chord.parse("Super+F12")],
            backend.read(),
        )
        assert chosen == Chord.parse("Super+F12")

    def test_niri_own_overlay_chord_is_seen_as_taken(self, env):
        backend = NiriBackend(config_root=env / "niri")
        assert not conflicts.is_available(
            Chord.parse("Mod+Shift+Slash"), backend.read()
        )


class TestInstallHotkey:
    def test_dry_run_changes_nothing(self, env, capsys):
        path = env / "niri" / "cfg" / "keybinds.kdl"
        before = path.read_text()
        cli.main(["install-hotkey", "--dry-run"])
        assert path.read_text() == before
        assert "would bind" in capsys.readouterr().out

    def test_installs_into_every_detected_compositor(self, env, capsys):
        assert cli.main(["install-hotkey"]) == 0
        out = capsys.readouterr().out
        assert out.count("bound") >= 3

    def test_avoids_niris_built_in_overlay_chord(self, env, capsys):
        cli.main(["install-hotkey"])
        backend = NiriBackend(config_root=env / "niri")
        mine = next(s for s in backend.read() if s.owner == "cachy-shortcuts")
        assert mine.chord != Chord.parse("Mod+Shift+Slash")

    def test_is_idempotent(self, env, capsys):
        cli.main(["install-hotkey"])
        capsys.readouterr()
        cli.main(["install-hotkey"])
        assert "already bound" in capsys.readouterr().out


class TestLearningMode:
    def test_lookups_accumulate(self, env):
        for _ in range(3):
            usage.record_lookup("super+b")
        usage.record_lookup("super+q")
        assert usage.counts()["super+b"] == 3

    def test_single_lookups_are_not_gaps(self, env):
        usage.record_lookup("super+q")
        assert usage.top_gaps() == []

    def test_repeated_lookups_surface_as_gaps(self, env):
        for _ in range(4):
            usage.record_lookup("super+b")
        for _ in range(2):
            usage.record_lookup("super+e")
        gaps = usage.top_gaps()
        assert [g.chord for g in gaps] == ["super+b", "super+e"]

    def test_forget_all_clears_history(self, env, capsys):
        usage.record_lookup("super+b")
        assert cli.main(["forget", "--all"]) == 0
        assert usage.counts() == {}

    def test_forget_requires_confirmation_flag(self, env, capsys):
        with pytest.raises(SystemExit):
            cli.main(["forget"])
        assert "--all" in capsys.readouterr().err


class TestAppScan:
    @pytest.fixture
    def apps(self, tmp_path, monkeypatch):
        share = tmp_path / "share" / "applications"
        share.mkdir(parents=True)
        (share / "firefox.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Firefox\nExec=firefox %u\n"
            "Icon=firefox\nCategories=Network;WebBrowser;\n"
        )
        (share / "hidden.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Secret\nExec=secret\nNoDisplay=true\n"
        )
        (share / "term.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Htop\nExec=htop\nTerminal=true\n"
        )
        (share / "link.desktop").write_text(
            "[Desktop Entry]\nType=Link\nName=Site\nURL=https://example.com\n"
        )
        (share / "actions.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Files\nExec=nautilus %U\n"
            "[Desktop Action new-window]\nName=New Window\nExec=nautilus --new-window\n"
        )
        monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "share"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "nonexistent"))
        return share

    def test_field_codes_are_stripped(self, apps):
        found = {a.name: a for a in appscan.scan()}
        assert found["Firefox"].command == "firefox"

    def test_nodisplay_entries_are_skipped(self, apps):
        assert "Secret" not in {a.name for a in appscan.scan()}

    def test_non_application_types_are_skipped(self, apps):
        assert "Site" not in {a.name for a in appscan.scan()}

    def test_terminal_apps_get_a_terminal(self, apps):
        found = {a.name: a for a in appscan.scan()}
        assert found["Htop"].command.startswith("xterm -e ")

    def test_desktop_actions_do_not_leak_into_the_main_entry(self, apps):
        found = {a.name: a for a in appscan.scan()}
        assert found["Files"].command == "nautilus"

    def test_search_ranks_exact_matches_first(self, apps):
        assert appscan.search("firefox")[0].name == "Firefox"

    def test_command_for_resolves_by_name(self, apps):
        assert appscan.command_for("firefox") == "firefox"
        assert appscan.command_for("nope") is None


class TestActionLabels:
    def test_niri_style_quoted_args_are_readable(self):
        action = 'spawn "wpctl" "set-volume" "@DEFAULT_AUDIO_SINK@" "5%+"'
        assert describe_action(action) == "wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%+"

    def test_shell_commands_survive(self):
        action = 'spawn-sh "qs -c noctalia-shell ipc call launcher toggle"'
        assert describe_action(action) == "qs -c noctalia-shell ipc call launcher toggle"

    def test_full_paths_reduce_to_the_binary(self):
        assert describe_action('spawn "/usr/bin/firefox"') == "firefox"

    def test_prefix_matching_requires_a_boundary(self):
        # "spawner" must not be mistaken for the "spawn" prefix.
        assert describe_action("spawner-tool") == "spawner-tool"

    def test_unbalanced_quotes_do_not_raise(self):
        assert describe_action('spawn-sh "grim -g "$(slurp)"') != ""

    def test_long_commands_are_truncated(self):
        action = "spawn " + " ".join(["averylongargument"] * 10)
        assert len(describe_action(action)) <= 52
