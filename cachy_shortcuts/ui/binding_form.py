"""The add/edit-a-binding form -- one screen, three fields, Tab between them.

All of the decisions (focus order, chord arming, suggestions, conflicts,
validation) live in ``form_model.BindingDraft`` and are tested without GTK.
This file only turns key events into draft calls and widgets into a picture of
the draft, so the part that can't be verified by running tests stays small.

Saving is the owner's job: ``on_save`` is handed the draft and returns an
error string, or None on success. That keeps the backend and ``editor`` out of
here entirely.
"""

from __future__ import annotations

from ._layershell import Gtk, Pango
from .chord_field import MOD_KEYVAL_NAMES, MOD_MASK, ChordField, chord_from_event
from .form_model import Field

_MAX_SUGGESTIONS = 6


class BindingForm(Gtk.Box):
    def __init__(self, on_save, on_cancel) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("form")
        self._on_save = on_save
        self._on_cancel = on_cancel
        self.draft = None
        self._syncing = False

        self.title_label = Gtk.Label(label="", xalign=0)
        self.title_label.add_css_class("form-title")
        self.append(self.title_label)

        self.command_entry = self._entry("Application name or command…")
        self.command_entry.connect("changed", self._on_command_changed)
        self.append(self._labelled("Application or command", self.command_entry))

        self.suggestion_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.suggestion_box.set_margin_top(4)
        self.append(self.suggestion_box)

        self.chord_field = ChordField(on_focus=lambda: self._focus_field(Field.CHORD))
        self.append(self._labelled("Shortcut", self.chord_field))

        self.note_label = Gtk.Label(label="", xalign=0)
        self.note_label.add_css_class("note")
        self.note_label.set_wrap(True)
        self.append(self.note_label)

        self.description_entry = self._entry("What it does (optional)…")
        self.description_entry.connect("changed", self._on_description_changed)
        self.append(self._labelled("Description", self.description_entry))

    # --- widget helpers ---------------------------------------------------

    @staticmethod
    def _entry(placeholder: str) -> Gtk.Entry:
        entry = Gtk.Entry()
        entry.add_css_class("field")
        entry.set_placeholder_text(placeholder)
        return entry

    @staticmethod
    def _labelled(text: str, widget: Gtk.Widget) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_margin_top(10)
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class("field-label")
        box.append(label)
        box.append(widget)
        return box

    # --- lifecycle --------------------------------------------------------

    def open(self, draft) -> None:
        self.draft = draft
        self.refresh(full=True)

    # --- rendering --------------------------------------------------------

    def refresh(self, full: bool = False) -> None:
        draft = self.draft
        if draft is None:
            return
        self._syncing = True
        try:
            self.title_label.set_label(draft.title)
            if full or self.command_entry.get_text() != draft.command:
                self.command_entry.set_text(draft.command)
                self.command_entry.set_position(-1)
            if full or self.description_entry.get_text() != draft.description:
                self.description_entry.set_text(draft.description)
            self.chord_field.update(draft)
            self._render_suggestions()
            self._render_note()
        finally:
            self._syncing = False
        self._sync_focus()

    def _render_suggestions(self) -> None:
        child = self.suggestion_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.suggestion_box.remove(child)
            child = nxt

        options = self.draft.suggestions(_MAX_SUGGESTIONS)
        self.suggestion_box.set_visible(bool(options))
        if not options:
            return
        chosen = self.draft.selected_suggestion()
        for app in options:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.add_css_class("suggestion")
            if app is chosen:
                row.add_css_class("selected")
            name = Gtk.Label(label=app.name, xalign=0)
            row.append(name)
            command = Gtk.Label(label=app.command, xalign=0)
            command.add_css_class("suggestion-command")
            command.set_ellipsize(Pango.EllipsizeMode.END)
            row.append(command)
            self.suggestion_box.append(row)

    def _render_note(self) -> None:
        draft = self.draft
        conflict = draft.conflict_message()
        warnings = draft.warnings()
        text = conflict or (warnings[0] if warnings else "")
        self.note_label.set_label(text)
        self.note_label.set_visible(bool(text))
        wants_warning = bool(text) and (not conflict or not draft.replace_confirmed)
        if wants_warning and not self.note_label.has_css_class("warning"):
            self.note_label.add_css_class("warning")
        elif not wants_warning and self.note_label.has_css_class("warning"):
            self.note_label.remove_css_class("warning")

    def _sync_focus(self) -> None:
        widget = {
            Field.COMMAND: self.command_entry,
            Field.CHORD: self.chord_field,
            Field.DESCRIPTION: self.description_entry,
        }[self.draft.focus]
        if not widget.has_focus():
            widget.grab_focus()

    # --- entry callbacks --------------------------------------------------

    def _on_command_changed(self, entry: Gtk.Entry) -> None:
        if self._syncing or self.draft is None:
            return
        self.draft.set_command(entry.get_text())
        # Deliberately not a full refresh: rewriting the entry's text under
        # the cursor while someone is typing into it is its own bug.
        self._render_suggestions()
        self._render_note()

    def _on_description_changed(self, entry: Gtk.Entry) -> None:
        if self._syncing or self.draft is None:
            return
        self.draft.description = entry.get_text()

    def _focus_field(self, target: Field) -> None:
        self.draft.focus_field(target)
        self.refresh()

    # --- input ------------------------------------------------------------

    def handle_key(self, keyval: int, state, name: str, ctrl: bool) -> bool:
        draft = self.draft
        if draft is None:
            return False

        # While the chord field is listening it owns almost everything, so
        # that Super+Q binds Super+Q instead of being read as a command.
        if draft.focus is Field.CHORD and draft.chord_armed:
            return self._handle_armed(keyval, state, name)

        if name == "Escape":
            self._on_cancel()
            return True
        if name == "Tab":
            # Tab out of the command field takes the highlighted app with it,
            # which is what fills in the free-chord suggestion.
            if draft.focus is Field.COMMAND and draft.accept_suggestion():
                draft.focus_field(Field.CHORD)
            else:
                draft.focus_next()
            self.refresh()
            return True
        if name == "ISO_Left_Tab":
            draft.focus_previous()
            self.refresh()
            return True
        if name in ("Down", "Up"):
            delta = 1 if name == "Down" else -1
            if draft.focus is Field.COMMAND and draft.suggestions():
                draft.move_suggestion(delta)
            elif delta > 0:
                draft.focus_next()
            else:
                draft.focus_previous()
            self.refresh()
            return True
        if name == "BackSpace" and draft.focus is Field.CHORD:
            draft.clear_chord()
            self.refresh()
            return True
        if name in ("Return", "KP_Enter"):
            self._handle_return(ctrl)
            return True
        if draft.focus is Field.CHORD and not ctrl:
            # An unarmed chord field: start listening rather than swallowing
            # the key, so pressing something here does the obvious thing. A
            # modifier on its own isn't that -- holding Super on the way to
            # Super+Q shouldn't count as the start of anything.
            if name in MOD_KEYVAL_NAMES:
                return True
            draft.arm_chord()
            return self._handle_armed(keyval, state, name)
        return False  # normal typing goes to whichever entry has focus

    def _handle_armed(self, keyval: int, state, name: str) -> bool:
        draft = self.draft
        # "Bare" Escape stops listening, but Super+Escape is a chord someone
        # might genuinely want, so only the unmodified press is a way out.
        bare = not (state & MOD_MASK)
        if name == "Escape" and bare:
            draft.disarm_chord()
            self.refresh()
            return True
        if name == "BackSpace" and bare:
            draft.clear_chord()
            self.refresh()
            return True
        chord = chord_from_event(keyval, state)
        if chord is None:
            return True  # a modifier on its own: keep listening
        draft.capture(chord)
        self.refresh()
        return True

    def _handle_return(self, ctrl: bool) -> None:
        draft = self.draft
        if ctrl:
            # Ctrl+Enter is "yes, take that chord from whoever has it".
            if draft.confirm_replace():
                self.refresh()
                return
        if draft.focus is Field.COMMAND:
            chosen = draft.selected_suggestion()
            if chosen is not None and chosen.command != draft.command:
                draft.accept_suggestion()
                draft.focus_field(Field.CHORD)
                self.refresh()
                return
        if not draft.can_save():
            # Land on the field that is holding the save up.
            draft.focus_field(
                Field.COMMAND if not draft.command.strip() else Field.CHORD
            )
            self.refresh()
            return
        self._on_save(draft)
