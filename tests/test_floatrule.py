"""Tiling-exception rules: rendering, idempotence, and the write path."""

from __future__ import annotations

import pytest

from cachy_shortcuts import APP_ID, APP_IDS, RULE_MARKER, editor, floatrule
from cachy_shortcuts.backends import CosmicBackend, MangoBackend, NiriBackend


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A scratch XDG_CONFIG_HOME/XDG_DATA_HOME so writes stay in tmp_path."""
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return config


# --- rendering ------------------------------------------------------------


def test_every_backend_offers_a_rule(all_backends):
    for backend in all_backends:
        rule = backend.float_rule()
        assert rule is not None, backend.name
        assert rule.marker in rule.body
        # Compared with escaping removed: niri writes the ids as regexes, so
        # `dev.cachyos.Shortcuts` appears there as `dev\.cachyos\.Shortcuts`.
        plain = rule.body.replace("\\", "")
        for app_id in APP_IDS:
            assert app_id in plain, (backend.name, app_id)


def test_niri_rule_is_a_window_rule_with_both_app_ids(niri):
    body = niri.float_rule().body
    assert "window-rule {" in body
    assert "open-floating true" in body
    # The dots are regex metacharacters and must be escaped; the hyphen is not.
    assert r'match app-id=r#"^dev\.cachyos\.Shortcuts$"#' in body
    assert 'match app-id=r#"^cachy-shortcuts$"#' in body


def test_niri_rule_targets_the_main_config_not_an_include(niri):
    """A window rule belongs in config.kdl, not the keybinds-only include."""
    assert niri.float_rule().path.name == "config.kdl"
    # ...even though new *bindings* go to the file that owns the binds block.
    assert niri.config_paths()[0].name == "keybinds.kdl"


def test_mango_rule_is_one_line_per_app_id(mango):
    lines = [
        ln for ln in mango.float_rule().body.splitlines() if ln.startswith("windowrule=")
    ]
    assert len(lines) == len(APP_IDS)
    assert all("isfloating:1" in ln for ln in lines)


def test_cosmic_rule_is_a_ron_list_entry(cosmic):
    rule = cosmic.float_rule()
    assert rule.mode == "ron-list"
    assert rule.path.name == "tiling_exception_custom"
    assert "CosmicSettings.WindowRules" in str(rule.path)
    assert f'( enabled: true, appid: "{APP_ID}", title: "" )' in rule.body
    # Both title forms, since an empty title's meaning is not documented.
    assert f'appid: "{APP_ID}", title: "Keybindings"' in rule.body


# --- applying -------------------------------------------------------------


def test_append_keeps_existing_content(niri):
    rule = niri.float_rule()
    original = 'binds {\n    Mod+T { spawn "foot"; }\n}\n'
    updated = rule.apply(original)
    assert updated.startswith(original.rstrip("\n"))
    assert "window-rule {" in updated


def test_apply_is_idempotent(all_backends):
    for backend in all_backends:
        rule = backend.float_rule()
        once = rule.apply("")
        assert rule.apply(once) == once, backend.name
        assert rule.installed_in(once)


def test_ron_list_splices_into_an_existing_list(cosmic):
    rule = cosmic.float_rule()
    original = '[\n    ( enabled: true, appid: "org.kde.kcalc", title: "KCalc", ),\n]\n'
    updated = rule.apply(original)
    assert "org.kde.kcalc" in updated
    assert updated.strip().startswith("[")
    assert updated.strip().endswith("]")
    assert APP_ID in updated


def test_ron_list_creates_the_list_when_absent(cosmic):
    updated = cosmic.float_rule().apply("")
    assert updated.strip().startswith("[")
    assert updated.strip().endswith("]")


def test_ron_list_does_not_double_the_separator(cosmic):
    """An entry already ending in a comma must not gain a second one."""
    rule = cosmic.float_rule()
    updated = rule.apply('[\n    ( enabled: true, appid: "a", title: "" ),\n]\n')
    assert ",," not in updated.replace(" ", "")


def test_empty_list_gains_no_leading_comma(cosmic):
    updated = cosmic.float_rule().apply("[\n]\n")
    assert "[,\n" not in updated.replace(" ", "")


def test_unknown_mode_is_rejected(niri):
    from dataclasses import replace

    with pytest.raises(ValueError, match="unknown float-rule mode"):
        replace(niri.float_rule(), mode="nonsense").apply("")


# --- installing -----------------------------------------------------------


def test_install_writes_and_reports(workspace):
    backend = NiriBackend()
    backend.config_paths()  # main config does not exist yet
    state = floatrule.install(backend)
    assert state.installed
    assert RULE_MARKER in state.rule.path.read_text()


def test_install_is_a_no_op_the_second_time(workspace):
    backend = MangoBackend()
    floatrule.install(backend)
    text = backend.float_rule().path.read_text()
    again = floatrule.install(backend)
    assert again.installed
    assert backend.float_rule().path.read_text() == text


def test_dry_run_writes_nothing(workspace):
    backend = NiriBackend()
    state = floatrule.install(backend, dry_run=True)
    assert not state.installed
    assert state.note == "would add"
    assert not state.rule.path.exists()


def test_status_for_reports_absence_then_presence(workspace):
    backend = CosmicBackend()
    assert not floatrule.status_for(backend).installed
    floatrule.install(backend)
    assert floatrule.status_for(backend).installed


def test_install_preserves_an_existing_config(workspace):
    backend = MangoBackend()
    path = backend.float_rule().path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("bind=SUPER,Return,spawn,foot\n")
    floatrule.install(backend)
    text = path.read_text()
    assert "bind=SUPER,Return,spawn,foot" in text
    assert "windowrule=isfloating:1" in text


def test_a_rejected_write_rolls_back_a_created_file(workspace, monkeypatch):
    """A validator that never passes must leave no file behind.

    backup.create() skips files that don't exist, so restoring its snapshot
    puts nothing back -- the rollback has to delete the file it created.
    """
    backend = NiriBackend()
    monkeypatch.setattr(floatrule, "_validator", lambda rule: (lambda text: False))
    with pytest.raises(editor.EditError):
        floatrule.install(backend)
    assert not backend.float_rule().path.exists()


def test_a_rejected_write_restores_previous_content(workspace, monkeypatch):
    backend = MangoBackend()
    path = backend.float_rule().path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("bind=SUPER,Return,spawn,foot\n")
    monkeypatch.setattr(floatrule, "_validator", lambda rule: (lambda text: False))
    with pytest.raises(editor.EditError):
        floatrule.install(backend)
    assert path.read_text() == "bind=SUPER,Return,spawn,foot\n"


def test_install_all_collects_failures_without_raising(workspace, monkeypatch):
    monkeypatch.setattr(floatrule, "_validator", lambda rule: (lambda text: False))
    states = floatrule.install_all([NiriBackend(), MangoBackend()])
    assert len(states) == 2
    assert all(not s.installed for s in states)
    assert all("rolled back" in s.note for s in states)


def test_ron_validator_rejects_a_broken_list(cosmic):
    check = floatrule._validator(cosmic.float_rule())
    body = cosmic.float_rule().body
    assert check(f"[\n{body}\n]\n")
    assert not check(f"{body}\n")  # no enclosing list
