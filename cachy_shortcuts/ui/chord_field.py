"""The one widget that has to listen to raw key presses.

It records while focused and stops the instant a real key arrives, which is
what keeps ``Tab``/``Enter``/``Esc`` usable in the rest of the form. The old
overlay listened in a modal state that never stopped, so the keys you needed
to leave it were the same keys it was trying to capture.
"""

from __future__ import annotations

from ._layershell import Gdk, Gtk
from ..model import Chord

# Modifier bits we treat as chord modifiers. Anything else (Num Lock, Caps
# Lock's lock bit, etc.) is masked off so it doesn't perturb the chord.
MOD_MASK = (
    Gdk.ModifierType.SHIFT_MASK
    | Gdk.ModifierType.CONTROL_MASK
    | Gdk.ModifierType.ALT_MASK
    | Gdk.ModifierType.SUPER_MASK
)

MOD_KEYVAL_NAMES = {
    "Shift_L", "Shift_R", "Control_L", "Control_R",
    "Alt_L", "Alt_R", "Super_L", "Super_R", "Meta_L", "Meta_R",
    "ISO_Level3_Shift", "Caps_Lock", "Num_Lock",
}


def chord_from_event(keyval: int, state: Gdk.ModifierType) -> Chord | None:
    """Turn a raw GDK key event into a canonical Chord, or None if it's bare."""
    name = Gdk.keyval_name(keyval) or ""
    if name in MOD_KEYVAL_NAMES:
        return None  # a modifier on its own is not a complete chord
    mods: list[str] = []
    masked = state & MOD_MASK
    if masked & Gdk.ModifierType.SUPER_MASK:
        mods.append("Super")
    if masked & Gdk.ModifierType.CONTROL_MASK:
        mods.append("Ctrl")
    if masked & Gdk.ModifierType.ALT_MASK:
        mods.append("Alt")
    if masked & Gdk.ModifierType.SHIFT_MASK:
        mods.append("Shift")
    lower = Gdk.keyval_to_lower(keyval)
    key_name = Gdk.keyval_name(lower) or name
    try:
        return Chord.from_parts(mods, key_name)
    except (KeyError, ValueError):
        return None


class ChordField(Gtk.Box):
    """Shows the draft's chord, and can hold focus so entries don't steal keys.

    Focusable but not an entry: while this has focus, typing goes nowhere by
    accident, and the form's own key controller decides what a press means.
    """

    def __init__(self, on_focus=None) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.add_css_class("chordfield")
        self.set_focusable(True)
        self.set_hexpand(True)

        self._label = Gtk.Label(label="")
        self._label.set_halign(Gtk.Align.START)
        self.append(self._label)

        if on_focus is not None:
            click = Gtk.GestureClick()
            click.connect("pressed", lambda *_a: on_focus())
            self.add_controller(click)

    def update(self, draft) -> None:
        self._label.set_label(draft.chord_text())
        wanted = set()
        if draft.chord is None:
            wanted.add("empty")
        if draft.chord_armed:
            wanted.add("armed")
        if draft.claimant() is not None and not draft.replace_confirmed:
            wanted.add("conflict")
        for css in ("armed", "conflict", "empty"):
            if css in wanted and not self.has_css_class(css):
                self.add_css_class(css)
            elif css not in wanted and self.has_css_class(css):
                self.remove_css_class(css)
