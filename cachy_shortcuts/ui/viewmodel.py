"""Overlay state, with no GTK in sight.

Filtering, grouping, selection and the edit state machine all live here so
they can be tested headlessly. The GTK layer is a thin renderer over this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from .. import conflicts, usage
from ..model import Category, Chord, Shortcut


class Mode(Enum):
    BROWSE = auto()
    CAPTURE_CHORD = auto()
    EDIT_COMMAND = auto()
    CONFIRM_DELETE = auto()


class RowKind(Enum):
    SECTION = auto()
    SHORTCUT = auto()


@dataclass
class Row:
    kind: RowKind
    title: str = ""
    shortcut: Shortcut | None = None

    @property
    def selectable(self) -> bool:
        return self.kind is RowKind.SHORTCUT


@dataclass
class OverlayModel:
    shortcuts: list[Shortcut] = field(default_factory=list)
    query: str = ""
    mode: Mode = Mode.BROWSE
    selected: int = 0
    app_context: str | None = None
    status: str = ""
    pending_chord: Chord | None = None
    draft_command: str = ""
    # None means "adding a brand-new binding"; otherwise the existing
    # shortcut being rebound/retargeted/deleted. Kept distinct from
    # `current()` because `n` (add) operates independently of whatever
    # row happens to be selected.
    editing_target: Shortcut | None = None
    _rows: list[Row] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.rebuild()

    # --- data ---------------------------------------------------------

    def set_shortcuts(self, shortcuts: list[Shortcut]) -> None:
        self.shortcuts = shortcuts
        self.rebuild()

    def matches(self, shortcut: Shortcut) -> bool:
        q = self.query.strip().lower()
        if not q:
            return True
        haystacks = (
            shortcut.chord.display().lower(),
            shortcut.chord.canonical.lower(),
            shortcut.label.lower(),
            shortcut.action.lower(),
        )
        # Every whitespace-separated term must match somewhere, so "sup b"
        # narrows rather than widening.
        return all(any(term in h for h in haystacks) for term in q.split())

    def rebuild(self) -> None:
        visible = [s for s in self.shortcuts if self.matches(s)]
        rows: list[Row] = []

        gaps = self._gap_rows(visible)
        if gaps and not self.query:
            rows.append(Row(RowKind.SECTION, "You keep looking these up"))
            rows.extend(Row(RowKind.SHORTCUT, shortcut=s) for s in gaps)

        for category in Category:
            group = [s for s in visible if s.category is category]
            if not group:
                continue
            rows.append(Row(RowKind.SECTION, category.value))
            group.sort(key=lambda s: (s.chord.display()))
            rows.extend(Row(RowKind.SHORTCUT, shortcut=s) for s in group)

        self._rows = rows
        self._clamp_selection()

    def _gap_rows(self, visible: list[Shortcut]) -> list[Shortcut]:
        """The bindings the user looks up most, surfaced at the top."""
        try:
            counts = usage.counts()
        except Exception:  # noqa: BLE001 - learning mode is never load-bearing
            return []
        scored = [
            (counts.get(s.chord.canonical, 0), s)
            for s in visible
            if counts.get(s.chord.canonical, 0) >= 2
        ]
        scored.sort(key=lambda pair: (-pair[0], pair[1].chord.display()))
        return [s for _, s in scored[:3]]

    # --- rows and selection -------------------------------------------

    @property
    def rows(self) -> list[Row]:
        return self._rows

    @property
    def selectable_indexes(self) -> list[int]:
        return [i for i, row in enumerate(self._rows) if row.selectable]

    def _clamp_selection(self) -> None:
        indexes = self.selectable_indexes
        if not indexes:
            self.selected = 0
            return
        if self.selected not in indexes:
            after = [i for i in indexes if i >= self.selected]
            self.selected = after[0] if after else indexes[-1]

    def move(self, delta: int) -> None:
        indexes = self.selectable_indexes
        if not indexes:
            return
        try:
            position = indexes.index(self.selected)
        except ValueError:
            position = 0
        # Clamp rather than wrap: wrapping in a long grouped list is
        # disorienting when you cannot see both ends at once.
        position = max(0, min(len(indexes) - 1, position + delta))
        self.selected = indexes[position]

    def current(self) -> Shortcut | None:
        if 0 <= self.selected < len(self._rows):
            return self._rows[self.selected].shortcut
        return None

    def set_query(self, query: str) -> None:
        self.query = query
        self.selected = 0
        self.rebuild()
        # Landing on a result after searching is the "I didn't remember this"
        # signal that drives learning mode.
        if query.strip():
            found = self.current()
            if found is not None:
                self._record(found)

    def _record(self, shortcut: Shortcut) -> None:
        try:
            usage.record_lookup(shortcut.chord.canonical)
        except Exception:  # noqa: BLE001 - never break the overlay over stats
            pass

    # --- edit state machine -------------------------------------------

    _READONLY_STATUS = (
        "That's a reference shortcut from an app cheat sheet — "
        "edit it in that app's own settings, not here."
    )

    def _refuse_if_readonly(self, target: Shortcut) -> bool:
        """True (and sets a status) if `target` cannot be edited here.

        App cheat-sheet entries carry no source span -- editor.py's write
        functions already refuse them, but failing that late (after a whole
        capture-and-commit flow) would be a bad user experience. Catching it
        here means the UI never even opens an edit flow for one.
        """
        if target.source is None:
            self.status = self._READONLY_STATUS
            return True
        return False

    def begin_capture(self) -> bool:
        """Rebind the currently selected shortcut to a new chord."""
        target = self.current()
        if target is None:
            return False
        if self._refuse_if_readonly(target):
            return False
        self.editing_target = target
        self.mode = Mode.CAPTURE_CHORD
        self.pending_chord = None
        self.status = "Press the new key combination…  Esc to cancel"
        return True

    def begin_add(self) -> bool:
        """Start a brand-new binding, independent of the current selection."""
        self.editing_target = None
        self.mode = Mode.CAPTURE_CHORD
        self.pending_chord = None
        self.status = "Press the key combination for a new binding…  Esc to cancel"
        return True

    def begin_command_edit(self) -> bool:
        target = self.current()
        if target is None:
            return False
        if self._refuse_if_readonly(target):
            return False
        self.editing_target = target
        self.mode = Mode.EDIT_COMMAND
        self.draft_command = target.action
        self.status = "Type a command or pick an app…  Esc to cancel"
        return True

    def begin_delete(self) -> bool:
        target = self.current()
        if target is None:
            return False
        if self._refuse_if_readonly(target):
            return False
        self.editing_target = target
        self.mode = Mode.CONFIRM_DELETE
        self.status = "Delete this binding?  y to confirm, Esc to cancel"
        return True

    def capture(self, chord: Chord) -> str | None:
        """Record a captured chord and report a conflict message, if any."""
        self.pending_chord = chord
        claim = self.conflict_for(chord)
        self.status = (
            f"{chord.display()}  —  {claim}" if claim else f"{chord.display()}  —  free"
        )
        return claim

    def cancel(self) -> None:
        self.mode = Mode.BROWSE
        self.pending_chord = None
        self.draft_command = ""
        self.editing_target = None
        self.status = ""

    def conflict_for(self, chord: Chord) -> str | None:
        """Whether `chord` is free, excluding the binding being edited.

        When adding a new binding (`editing_target is None`) nothing is
        excluded, since every existing shortcut is a real claimant.
        """
        others = [s for s in self.shortcuts if s is not self.editing_target]
        return conflicts.describe_claimant(chord, others)

    # --- presentation -------------------------------------------------

    def hint(self) -> str:
        if self.mode is Mode.CAPTURE_CHORD:
            return "esc cancel · enter save"
        if self.mode is Mode.EDIT_COMMAND:
            return "esc cancel · enter save · tab complete app"
        if self.mode is Mode.CONFIRM_DELETE:
            return "y delete · esc cancel"
        return "↑↓ move · enter rebind · ^enter command · n new · d delete · esc close"

    def header(self) -> str:
        if self.app_context:
            return f"Keybindings · {self.app_context}"
        return "Keybindings"

    def count(self) -> int:
        return sum(1 for row in self._rows if row.selectable)
