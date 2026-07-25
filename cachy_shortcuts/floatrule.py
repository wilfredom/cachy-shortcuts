"""Install the tiling exception that keeps the overlay from being tiled.

The overlay is meant to be a layer-shell surface, which no tiling compositor
lays out at all -- it just floats above everything. When gtk4-layer-shell is
missing (or the compositor doesn't implement wlr-layer-shell) it falls back to
an ordinary toplevel window, and a tiler then does what tilers do: puts it in a
column next to your terminal. These rules are the safety net for that path.

Each rule carries a ``cachy-shortcuts`` marker comment, so installing twice is
a no-op and a curious user reading their own config can see where it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import editor
from .backends.base import Backend, FloatRule


@dataclass
class RuleStatus:
    backend: Backend
    rule: FloatRule | None
    installed: bool
    note: str = ""

    @property
    def path(self) -> Path | None:
        return self.rule.path if self.rule else None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def status_for(backend: Backend) -> RuleStatus:
    rule = backend.float_rule()
    if rule is None:
        return RuleStatus(backend, None, False, "no tiling exception needed")
    return RuleStatus(backend, rule, rule.installed_in(_read(rule.path)))


def _validator(rule: FloatRule):
    """What counts as "the write took".

    Presence of the rule text is the point, but a structural check catches the
    case where we spliced into a RON list and produced something COSMIC would
    refuse to load -- better to roll that back than to leave a config the
    settings daemon silently ignores.
    """

    def check(written: str) -> bool:
        if not rule.installed_in(written):
            return False
        if rule.mode == "ron-list":
            stripped = written.strip()
            return stripped.startswith("[") and stripped.endswith("]")
        return True

    return check


def install(backend: Backend, dry_run: bool = False) -> RuleStatus:
    """Add ``backend``'s float rule if it isn't already there."""
    state = status_for(backend)
    if state.rule is None or state.installed:
        return state

    rule = state.rule
    text = _read(rule.path)
    new_text = rule.apply(text)
    if new_text == text:
        return RuleStatus(backend, rule, True)
    if dry_run:
        return RuleStatus(backend, rule, False, "would add")

    result = editor.write_file(
        rule.path, new_text, f"float-rule ({backend.name})", _validator(rule)
    )
    backend.reload()
    return RuleStatus(backend, rule, True, f"added to {result.path}")


def install_all(backends: list[Backend], dry_run: bool = False) -> list[RuleStatus]:
    out: list[RuleStatus] = []
    for backend in backends:
        try:
            out.append(install(backend, dry_run=dry_run))
        except editor.EditError as exc:
            out.append(RuleStatus(backend, backend.float_rule(), False, str(exc)))
    return out
