"""Register this tool's own hotkey in every compositor that's installed.

The chord is chosen by the same conflict detector the overlay uses, so setup
can't silently steal a binding the user (or Noctalia/DMS) already relies on.
"""

from __future__ import annotations

from . import conflicts, detect, editor, floatrule
from .backends.base import Backend
from .model import Chord

# Tried in order. Mod+Shift+Slash is deliberately absent: niri already binds it
# to its own hotkey overlay, and taking it would shadow a built-in.
CANDIDATE_CHORDS = (
    "Super+Slash",
    "Super+Shift+K",
    "Super+Ctrl+Slash",
    "Super+F1",
    "Super+Shift+F1",
)

LABEL = "Keybinding atlas"


def _action_for(backend: Backend) -> str:
    if backend.name == "niri":
        return 'spawn-sh "cachy-shortcuts overlay --toggle"'
    if backend.name == "mango":
        return "spawn cachy-shortcuts overlay --toggle"
    return "cachy-shortcuts overlay --toggle"


def install_hotkey(chord_text: str | None = None, dry_run: bool = False) -> int:
    backends = detect.detect_installed()
    if not backends:
        print("No compositor configs found; nothing to register.")
        return 1

    candidates_text = [chord_text] if chord_text else list(CANDIDATE_CHORDS)
    try:
        candidates = [Chord.parse(c) for c in candidates_text]
    except (KeyError, ValueError) as exc:
        print(f"error: bad chord {chord_text!r}: {exc}")
        return 1

    failures = 0
    for backend in backends:
        existing = backend.read()

        # Already installed? Leave it alone and report where it is.
        mine = next((s for s in existing if s.owner == "cachy-shortcuts"), None)
        if mine is not None:
            print(f"{backend.display_name}: already bound to {mine.chord.display()}")
            continue

        chord = conflicts.first_free(candidates, existing)
        if chord is None:
            taken = ", ".join(c.display() for c in candidates)
            print(
                f"{backend.display_name}: every candidate is taken ({taken}). "
                "Pass --chord to choose one explicitly."
            )
            failures += 1
            continue

        if dry_run:
            print(f"{backend.display_name}: would bind {chord.display()}")
            continue

        try:
            result = editor.add(backend, chord, _action_for(backend), LABEL)
        except editor.EditError as exc:
            print(f"{backend.display_name}: failed - {exc}")
            failures += 1
            continue
        print(f"{backend.display_name}: bound {chord.display()}  ({result.path})")

    failures += install_rules(dry_run=dry_run)

    if not dry_run and failures == 0:
        print("\nPress your new hotkey to open the overlay.")
    return 1 if failures else 0


def install_rules(dry_run: bool = False) -> int:
    """Add each compositor's tiling exception. Returns the failure count."""
    backends = detect.detect_installed()
    if not backends:
        print("No compositor configs found; no tiling exception to add.")
        return 1

    failures = 0
    print("\nTiling exceptions:")
    for state in floatrule.install_all(backends, dry_run=dry_run):
        name = state.backend.display_name
        if state.rule is None:
            print(f"  {name}: {state.note}")
        elif state.installed and state.note:
            print(f"  {name}: {state.note}")
        elif state.installed:
            print(f"  {name}: already exempt from tiling ({state.rule.path})")
        elif dry_run:
            print(f"  {name}: would add a float rule to {state.rule.path}")
        else:
            print(f"  {name}: failed - {state.note}")
            failures += 1
    return failures
