import json

import pytest

from cachy_shortcuts import labels
from cachy_shortcuts.labels import LabelError
from cachy_shortcuts.model import Chord, Shortcut, describe_action


@pytest.fixture(autouse=True)
def isolated_backups(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def sc(action, description=""):
    return Shortcut(chord=Chord.parse("Super+X") if hasattr(Chord, "parse") else None,
                    action=action, description=description)


def runner_for(mapping):
    calls = []

    def run(prompt):
        calls.append(prompt)
        return json.dumps(mapping)
    run.calls = calls
    return run


def test_key_stable_across_whitespace():
    assert labels.key('spawn  "a"\t"b"') == labels.key(' spawn "a" "b" ')
    assert labels.key("a") != labels.key("b")


def test_precedence():
    bare, described = sc("spawn foo"), sc("spawn foo", "Mine")
    assert bare.label == describe_action("spawn foo")
    labels.set_user("spawn foo", "Open foo")
    labels.annotate([bare, described])
    assert bare.label == "Open foo"
    assert described.label == "Mine" and described.friendly == ""


def test_user_label_survives_generate():
    labels.set_user("spawn foo", "Mine")
    r = runner_for({"spawn baz": "Baz"})
    labels.generate([sc("spawn foo"), sc("spawn baz")], runner=r)
    assert "spawn foo" not in r.calls[0].split("\n\n", 1)[1]
    assert labels.get("spawn foo") == "Mine"
    assert labels.get("spawn baz") == "Baz"


def test_user_entry_written_mid_flight_not_overwritten():
    s = sc("spawn foo")

    def run(prompt):
        labels.set_user("spawn foo", "Mine")
        return json.dumps({"spawn foo": "LLM"})
    res = labels.generate([s], runner=run)
    assert labels.get("spawn foo") == "Mine" and res.count == 0


def test_bad_json_writes_nothing():
    with pytest.raises(LabelError):
        labels.generate([sc("spawn foo")], runner=lambda p: "sorry, no")
    with pytest.raises(LabelError):
        labels.generate([sc("spawn foo")], runner=lambda p: "{not json}")
    assert not labels.store_path().exists()


def test_fenced_json_parses():
    r = lambda p: '```json\n{"spawn foo": "Open foo"}\n```'
    res = labels.generate([sc("spawn foo")], runner=r)
    assert res.count == 1 and res.written
    assert labels.get("spawn foo") == "Open foo"


def test_unknown_key_rejects_whole_reply():
    with pytest.raises(LabelError):
        labels.generate([sc("spawn foo")],
                        runner=runner_for({"spawn foo": "Ok", "invented": "X"}))
    assert not labels.store_path().exists()


def test_label_clipped_and_empty_rejected():
    labels.generate([sc("a")], runner=runner_for({"a": "x" * 80}))
    assert len(labels.get("a")) == 32
    with pytest.raises(LabelError):
        labels.generate([sc("b")], runner=runner_for({"b": "  "}))


def test_dry_run_does_not_call_runner():
    r = runner_for({})
    res = labels.generate([sc("spawn foo")], runner=r, dry_run=True)
    assert r.calls == [] and res.count == 1 and res.prompt_chars > 0
    assert not res.written and not labels.store_path().exists()


def test_pending_excludes_labelled_and_described():
    labels.set_user("have", "Label")
    shortcuts = [sc("have"), sc("need"), sc("need"), sc("desc", "Has one")]
    assert labels.pending(shortcuts) == ["need"]


def test_corrupt_store_tolerated():
    p = labels.store_path()
    p.parent.mkdir(parents=True)
    p.write_text("{garbage")
    assert labels.get("x") is None
    labels.set_user("x", "Y")
    assert labels.get("x") == "Y"


def test_missing_claude_binary(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError
    monkeypatch.setattr(labels.subprocess, "run", boom)
    with pytest.raises(LabelError, match="claude"):
        labels.generate([sc("spawn foo")])


@pytest.mark.parametrize("fixture", ["niri", "hyprland"])
def test_real_fixture_roundtrip(request, fixture):
    shortcuts = request.getfixturevalue(fixture).read()
    todo = labels.pending(shortcuts)
    assert todo
    labels.generate(shortcuts, runner=runner_for({todo[0]: "Do thing"}))
    labels.annotate(shortcuts)
    hit = [s for s in shortcuts if s.action == todo[0]]
    assert all(s.label == "Do thing" for s in hit if not s.description)
    assert todo[0] not in labels.pending(shortcuts)
