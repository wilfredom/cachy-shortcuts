"""Browse state for the overlay, with no GTK in sight.

Filtering, grouping and selection live here so they can be tested headlessly.
Editing used to live here too; it now lives in ``form_model.BindingDraft``,
which this module opens and closes but does not otherwise know about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from .. import usage
from ..appscan import DesktopApp
from ..model import Category, Shortcut
from .form_model import BindingDraft


class Mode(Enum):
    BROWSE = auto()
    FORM = auto()
    CONFIRM_DELETE = auto()


class RowKind(Enum):
    SECTION = auto()
    SHORTCUT = auto()
    # "Bind <query>…" -- the way out of a search that found nothing.
    CREATE = auto()


@dataclass
class Row:
    kind: RowKind
    title: str = ""
    shortcut: Shortcut | None = None

    @property
    def selectable(self) -> bool:
        return self.kind in (RowKind.SHORTCUT, RowKind.CREATE)


@dataclass
class OverlayModel:
    shortcuts: list[Shortcut] = field(default_factory=list)
    query: str = ""
    mode: Mode = Mode.BROWSE
    selected: int = 0
    app_context: str | None = None
    status: str = ""
    # Installed apps, scanned once and handed to each draft so the form's
    # type-ahead doesn't re-glob every applications directory per keystroke.
    apps: list[DesktopApp] = field(default_factory=list)
    draft: BindingDraft | None = None
    delete_target: Shortcut | None = None
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

        # A search that found nothing is the moment you most want to create
        # the thing you were looking for, so offer it right there.
        if self.query.strip() and not visible:
            rows.append(Row(RowKind.CREATE, title=self.query.strip()))

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

    def move_page(self, delta: int, page: int = 8) -> None:
        self.move(delta * max(1, page))

    def move_to_edge(self, direction: int) -> None:
        indexes = self.selectable_indexes
        if not indexes:
            return
        self.selected = indexes[-1] if direction > 0 else indexes[0]

    def current(self) -> Shortcut | None:
        if 0 <= self.selected < len(self._rows):
            return self._rows[self.selected].shortcut
        return None

    def current_row(self) -> Row | None:
        if 0 <= self.selected < len(self._rows):
            return self._rows[self.selected]
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

    # --- opening and closing the form ---------------------------------

    _READONLY_STATUS = (
        "That's a reference shortcut from an app cheat sheet — "
        "edit it in that app's own settings, not here."
    )

    def _refuse_if_readonly(self, target: Shortcut) -> bool:
        """True (and sets a status) if `target` cannot be edited here.

        App cheat-sheet entries carry no source span -- editor.py's write
        functions already refuse them, but failing that late (after a whole
        form has been filled in) would be a bad user experience. Catching it
        here means the UI never even opens an edit flow for one.
        """
        if target.source is None:
            self.status = self._READONLY_STATUS
            return True
        return False

    def begin_add(self, command: str = "") -> bool:
        """Start a brand-new binding, independent of the current selection."""
        self.draft = BindingDraft.for_new(self.shortcuts, self.apps, command=command)
        self.mode = Mode.FORM
        self.status = ""
        return True

    def begin_edit(self, unwrap=None) -> bool:
        """Edit the selected binding.

        ``unwrap`` turns a backend action back into a bare command (so the
        form shows ``firefox``, not ``spawn-sh "firefox"``); None means the
        action is shown verbatim and saved back the same way.
        """
        target = self.current()
        if target is None:
            return False
        if self._refuse_if_readonly(target):
            return False
        command = unwrap(target.action) if unwrap is not None else None
        self.draft = BindingDraft.for_edit(self.shortcuts, target, command, self.apps)
        self.mode = Mode.FORM
        self.status = ""
        return True

    def begin_delete(self) -> bool:
        target = self.current()
        if target is None:
            return False
        if self._refuse_if_readonly(target):
            return False
        self.delete_target = target
        self.mode = Mode.CONFIRM_DELETE
        self.status = f"Delete {target.chord.display()}?  y to confirm, Esc to cancel"
        return True

    def activate(self, unwrap=None) -> bool:
        """What Enter does on the current row: create, or edit."""
        row = self.current_row()
        if row is None:
            return False
        if row.kind is RowKind.CREATE:
            return self.begin_add(command=row.title)
        return self.begin_edit(unwrap=unwrap)

    def cancel(self) -> None:
        self.mode = Mode.BROWSE
        self.draft = None
        self.delete_target = None
        self.status = ""

    # --- presentation -------------------------------------------------

    def hint(self) -> str:
        if self.mode is Mode.FORM and self.draft is not None:
            return self.draft.hint()
        if self.mode is Mode.CONFIRM_DELETE:
            return "y delete · esc cancel"
        return "↑↓ move · enter edit · ^n new · ^d delete · esc close"

    def header(self) -> str:
        if self.app_context:
            return f"Keybindings · {self.app_context}"
        return "Keybindings"

    def count(self) -> int:
        return sum(1 for row in self._rows if row.kind is RowKind.SHORTCUT)
