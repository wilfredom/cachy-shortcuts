"""State for the add/edit-a-binding form, with no GTK in sight.

The previous overlay layered three modal keyboard states over the list --
capture a chord, then type a command, then confirm -- and each state ate the
keys you needed to get out of it. This replaces that with one form you Tab
through, and puts all of its logic here so it can be tested headlessly.

Two deliberate choices:

* **Command first, chord second.** You know *what* you want to bind before you
  know which keys are still free. Picking an app then suggests an unclaimed
  chord derived from its name, so the common case is: type a few letters,
  accept the suggestion, press Enter.
* **A claimed chord blocks the save.** ``editor.add`` appends unconditionally,
  so binding a chord something else already owns used to produce a silent
  duplicate that the compositor ignores -- exactly the failure this tool
  exists to detect. Taking a chord is now something you confirm, and it goes
  through ``editor.take_over`` so the old binding is actually removed.
"""

from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass, field
from enum import Enum, auto

from .. import conflicts
from ..appscan import DesktopApp, rank
from ..model import Chord, Shortcut


class Field(Enum):
    COMMAND = auto()
    CHORD = auto()
    DESCRIPTION = auto()


FIELD_ORDER: tuple[Field, ...] = (Field.COMMAND, Field.CHORD, Field.DESCRIPTION)

# Modifier sets tried when proposing a chord for an app, in the order a user
# would reach for them.
_SUGGESTION_MODS: tuple[tuple[str, ...], ...] = (
    ("Super",),
    ("Super", "Shift"),
    ("Super", "Alt"),
    ("Super", "Ctrl"),
)


def suggest_chord(name: str, existing: list[Shortcut]) -> Chord | None:
    """An unclaimed chord derived from ``name``, or None if nothing is free.

    Tries ``Super`` plus each letter of the name in turn before reaching for
    a second modifier, so the offer for "Obsidian" is Super+O rather than
    something no one would have chosen by hand.
    """
    letters: list[str] = []
    for char in name.lower():
        if char.isascii() and char.isalnum() and char not in letters:
            letters.append(char)
    if not letters:
        return None
    candidates: list[Chord] = []
    for mods in _SUGGESTION_MODS:
        for letter in letters:
            try:
                candidates.append(Chord.from_parts(mods, letter))
            except (KeyError, ValueError):
                continue
    return conflicts.first_free(candidates, existing)


@dataclass
class BindingDraft:
    """One in-progress add or edit."""

    # Everything currently bound, for conflict checks and chord suggestions.
    existing: list[Shortcut] = field(default_factory=list)
    # None means "adding a new binding"; otherwise the one being edited.
    target: Shortcut | None = None
    # Installed apps, scanned once so the type-ahead can re-rank without I/O.
    apps: list[DesktopApp] = field(default_factory=list)

    command: str = ""
    chord: Chord | None = None
    description: str = ""

    # A native compositor action (close-window, Move(Left)) must not be
    # re-wrapped in the backend's spawn syntax when it's saved back.
    spawns: bool = True

    focus: Field = Field.COMMAND
    chord_armed: bool = False
    suggestion_index: int = 0
    # Set once the user has explicitly agreed to take a claimed chord.
    replace_confirmed: bool = False

    # --- construction --------------------------------------------------

    @classmethod
    def for_new(
        cls,
        existing: list[Shortcut],
        apps: list[DesktopApp] | None = None,
        command: str = "",
    ) -> "BindingDraft":
        draft = cls(existing=list(existing), apps=list(apps or []), command=command)
        draft.focus = Field.COMMAND
        return draft

    @classmethod
    def for_edit(
        cls,
        existing: list[Shortcut],
        target: Shortcut,
        command: str | None,
        apps: list[DesktopApp] | None = None,
    ) -> "BindingDraft":
        """``command`` is the unwrapped command, or None for a native action."""
        return cls(
            existing=list(existing),
            target=target,
            apps=list(apps or []),
            command=command if command is not None else target.action,
            chord=target.chord,
            description=target.description,
            spawns=command is not None,
            focus=Field.COMMAND,
        )

    @property
    def is_new(self) -> bool:
        return self.target is None

    @property
    def title(self) -> str:
        return "New binding" if self.is_new else "Edit binding"

    # --- fields --------------------------------------------------------

    def focus_next(self) -> None:
        self._move_focus(1)

    def focus_previous(self) -> None:
        self._move_focus(-1)

    def _move_focus(self, delta: int) -> None:
        position = FIELD_ORDER.index(self.focus)
        self.focus = FIELD_ORDER[(position + delta) % len(FIELD_ORDER)]
        # Arriving at an empty chord field means you are there to fill it in.
        self.chord_armed = self.focus is Field.CHORD and self.chord is None

    def focus_field(self, target: Field) -> None:
        self.focus = target
        self.chord_armed = target is Field.CHORD and self.chord is None

    def set_command(self, text: str) -> None:
        if text == self.command:
            return
        self.command = text
        self.suggestion_index = 0

    # --- app type-ahead ------------------------------------------------

    def suggestions(self, limit: int = 6) -> list[DesktopApp]:
        """Installed apps matching what's typed so far.

        Suppressed once the field holds something with arguments or a path:
        that is a command line, not a half-typed app name, and offering to
        replace it would be in the way.
        """
        query = self.command.strip()
        if not query or self.focus is not Field.COMMAND:
            return []
        if "/" in query or " " in query:
            return []
        return rank(self.apps, query, limit)

    def selected_suggestion(self) -> DesktopApp | None:
        options = self.suggestions()
        if not options:
            return None
        return options[self.suggestion_index % len(options)]

    def move_suggestion(self, delta: int) -> None:
        options = self.suggestions()
        if not options:
            return
        self.suggestion_index = (self.suggestion_index + delta) % len(options)

    def accept_suggestion(self) -> bool:
        """Take the highlighted app, and propose a free chord for it.

        The chord is only suggested when the field is still empty, so this
        never overwrites a chord you deliberately pressed.
        """
        app = self.selected_suggestion()
        if app is None:
            return False
        self.command = app.command
        self.spawns = True
        self.suggestion_index = 0
        if not self.description:
            self.description = app.name
        if self.chord is None:
            self.chord = suggest_chord(app.name, self.existing)
        return True

    # --- chord ---------------------------------------------------------

    def arm_chord(self) -> None:
        self.chord_armed = True

    def disarm_chord(self) -> None:
        self.chord_armed = False

    def capture(self, chord: Chord) -> None:
        """Record a pressed chord and stop listening.

        Disarming here is the whole point of the record-on-focus design: once
        the chord is captured, Tab / Enter / Esc go back to meaning what they
        mean everywhere else instead of being swallowed by the capture.
        """
        self.chord = chord
        self.chord_armed = False
        self.replace_confirmed = False

    def clear_chord(self) -> None:
        self.chord = None
        self.chord_armed = True
        self.replace_confirmed = False

    def chord_text(self) -> str:
        if self.chord is not None:
            return self.chord.display()
        return "press a key combination…" if self.chord_armed else "not set"

    # --- conflicts and validation --------------------------------------

    def claimant(self) -> Shortcut | None:
        """The binding that already owns this chord, if any."""
        if self.chord is None:
            return None
        others = [s for s in self.existing if s is not self.target]
        return conflicts.claimant(self.chord, others)

    def conflict_message(self) -> str | None:
        owner = self.claimant()
        if owner is None:
            return None
        who = owner.owner or owner.label or "another binding"
        if self.replace_confirmed:
            return f"will replace: {who}"
        return f"already bound: {who}  —  ctrl+enter to take it"

    def confirm_replace(self) -> bool:
        if self.claimant() is None:
            return False
        self.replace_confirmed = True
        return True

    def blockers(self) -> list[str]:
        """Reasons the form cannot be saved yet."""
        out: list[str] = []
        if not self.command.strip():
            out.append("a command is required")
        if self.chord is None:
            out.append("a key combination is required")
        if self.claimant() is not None and not self.replace_confirmed:
            out.append(self.conflict_message() or "chord already bound")
        return out

    def warnings(self) -> list[str]:
        """Things worth saying that are not reasons to refuse the save."""
        out: list[str] = []
        binary = self._binary()
        if binary and not shutil.which(binary):
            out.append(f"{binary} isn't on PATH — the binding will do nothing")
        return out

    def _binary(self) -> str | None:
        if not self.spawns:
            return None
        text = self.command.strip()
        if not text:
            return None
        try:
            parts = shlex.split(text)
        except ValueError:
            parts = text.split()
        if not parts:
            return None
        first = parts[0]
        # An assignment prefix (FOO=bar cmd) or a shell operator means this is
        # a snippet rather than a plain binary, and there is nothing to check.
        if "=" in first or first in ("sh", "bash", "env"):
            return None
        return first

    def can_save(self) -> bool:
        return not self.blockers()

    # --- what to write --------------------------------------------------

    def hint(self) -> str:
        if self.focus is Field.COMMAND and self.suggestions():
            return "↑↓ pick app · tab accept · enter save · esc cancel"
        if self.focus is Field.CHORD and self.chord_armed:
            return "press a combination · backspace clear · esc stop listening"
        if self.claimant() is not None and not self.replace_confirmed:
            return "^enter take the chord · tab next field · esc cancel"
        return "tab next field · enter save · esc cancel"
