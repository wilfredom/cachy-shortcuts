"""Friendly labels for shortcuts that carry no description.

Most bindings are bare commands (``spawn "foo" "--bar"``) with no comment, so
the overlay falls back to a mechanical description. A label can be written by
hand or generated in one batch by an LLM (``claude -p``). Labels live in a JSON
sidecar keyed by a hash of the whitespace-normalised action, so the same
command gets the same label on every compositor. A label never overrides a
description the config itself provides, and a user-written label is never
overwritten by a generated one.

Generation is opt-in and is the only thing here that leaves the machine: the
action strings are sent to the model. ``dry_run`` shows what would be sent.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .backup import data_dir

MAX_LABEL = 32


class LabelError(Exception):
    """Generation failed; nothing was written."""


@dataclass
class GenerateResult:
    count: int
    prompt_chars: int
    written: bool


def store_path() -> Path:
    return data_dir() / "labels.json"


def _load() -> dict:
    try:
        data = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"labels": {}}
    if not isinstance(data, dict) or not isinstance(data.get("labels"), dict):
        return {"labels": {}}
    return data


def _save(data: dict) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def key(action: str) -> str:
    return hashlib.sha1(" ".join(action.split()).encode("utf-8")).hexdigest()


def _entry(data: dict, action: str) -> dict | None:
    e = data["labels"].get(key(action))
    if isinstance(e, dict) and isinstance(e.get("label"), str) and e["label"]:
        return e
    return None


def get(action: str) -> str | None:
    e = _entry(_load(), action)
    return e["label"] if e else None


def set_user(action: str, label: str) -> None:
    label = label.strip()[:MAX_LABEL]
    if not label:
        raise ValueError("label is empty")
    data = _load()
    data["labels"][key(action)] = {"label": label, "by": "user"}
    _save(data)


def remove(action: str) -> bool:
    data = _load()
    if key(action) in data["labels"]:
        del data["labels"][key(action)]
        _save(data)
        return True
    return False


def annotate(shortcuts) -> None:
    """Set ``friendly`` on shortcuts with no description."""
    data = _load()
    for s in shortcuts:
        if s.description:
            continue
        e = _entry(data, s.action)
        if e:
            s.friendly = e["label"]


def pending(shortcuts) -> list[str]:
    """Unique actions with neither a description nor a stored label."""
    data = _load()
    return sorted(
        {s.action for s in shortcuts if not s.description and not _entry(data, s.action)}
    )


def build_prompt(actions: list[str]) -> str:
    return (
        "Below is a JSON list of keyboard-shortcut actions from a window manager "
        "config. For each one write a short label for a cheat sheet.\n"
        "Rules: at most 32 characters, verb first, plain words, no trailing "
        "period.\n"
        "Reply with ONLY a JSON object mapping each action string, copied "
        "exactly, to its label. No prose, no code fences.\n\n"
        + json.dumps(actions, indent=2, ensure_ascii=False)
    )


def _claude_runner(prompt: str) -> str:
    try:
        proc = subprocess.run(
            ["claude", "-p", "--model", "haiku", prompt],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError:
        raise LabelError("the `claude` command was not found; install Claude Code "
                         "or label shortcuts by hand") from None
    except subprocess.TimeoutExpired:
        raise LabelError("`claude` timed out after 120s") from None
    if proc.returncode != 0:
        raise LabelError(f"`claude` failed: {(proc.stderr or '').strip()[:200]}")
    return proc.stdout


def _parse_reply(reply: str, sent: list[str]) -> dict[str, str]:
    text = re.sub(r"```(?:json)?", "", reply)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise LabelError("the reply contained no JSON object")
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LabelError(f"the reply was not valid JSON: {e}") from None
    if not isinstance(obj, dict):
        raise LabelError("the reply was not a JSON object")
    allowed = set(sent)
    out: dict[str, str] = {}
    for action, label in obj.items():
        if action not in allowed:
            raise LabelError(f"the reply labelled an action that was not sent: {action[:60]!r}")
        if not isinstance(label, str) or not label.strip():
            raise LabelError(f"empty or non-text label for {action[:60]!r}")
        out[action] = label.strip()[:MAX_LABEL].rstrip()
    return out


def generate(shortcuts, runner: Callable[[str], str] | None = None,
             dry_run: bool = False) -> GenerateResult:
    """Label every pending action in one model call."""
    actions = pending(shortcuts)
    if not actions:
        return GenerateResult(0, 0, False)
    prompt = build_prompt(actions)
    if dry_run:
        return GenerateResult(len(actions), len(prompt), False)
    labels = _parse_reply((runner or _claude_runner)(prompt), actions)
    data = _load()
    count = 0
    for action, label in labels.items():
        e = _entry(data, action)
        if e and e.get("by") == "user":
            continue
        data["labels"][key(action)] = {"label": label, "by": "llm"}
        count += 1
    if count:
        _save(data)
    return GenerateResult(count, len(prompt), bool(count))
