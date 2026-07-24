"""GTK4 layer-shell overlay -- the visible half of the tool.

All decision logic (filtering, grouping, selection, the edit state machine)
lives in ``viewmodel.py`` and is unit-tested there without GTK. This module is
deliberately thin: it turns GDK key events into viewmodel calls and rebuilds
widgets from ``model.rows`` after every change.

Honest limitation: this file cannot be run or visually verified in the
container that built it (no Wayland session, no GTK4/gtk4-layer-shell
installed here). It is written against the documented gtk4-layer-shell API
and kept as thin as possible over the already-tested viewmodel so that the
surface area which *can't* be verified by execution is as small as it can be.
Verify on the real machine with ``cachy-shortcuts doctor`` first (it reports
whether PyGObject/GTK4/gtk4-layer-shell are actually importable) and then
``cachy-shortcuts overlay``.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

    _HAS_LAYER_SHELL = True
except (ValueError, ImportError):
    LayerShell = None  # type: ignore[assignment]
    _HAS_LAYER_SHELL = False

from .. import appscan, cheatsheets, detect, editor, theming
from ..model import Chord
from .style import stylesheet
from .viewmodel import Mode, OverlayModel, RowKind

APP_ID = "dev.cachyos.Shortcuts"

# Modifier bits we treat as chord modifiers. Anything else (Num Lock, Caps
# Lock's lock bit, etc.) is masked off so it doesn't perturb the chord.
_MOD_MASK = (
    Gdk.ModifierType.SHIFT_MASK
    | Gdk.ModifierType.CONTROL_MASK
    | Gdk.ModifierType.ALT_MASK
    | Gdk.ModifierType.SUPER_MASK
)

_MOD_KEYVAL_NAMES = {
    "Shift_L", "Shift_R", "Control_L", "Control_R",
    "Alt_L", "Alt_R", "Super_L", "Super_R", "Meta_L", "Meta_R",
    "ISO_Level3_Shift", "Caps_Lock", "Num_Lock",
}


def _chord_from_event(keyval: int, state: Gdk.ModifierType) -> Chord | None:
    """Turn a raw GDK key event into a canonical Chord, or None if it's bare."""
    name = Gdk.keyval_name(keyval) or ""
    if name in _MOD_KEYVAL_NAMES:
        return None  # a modifier on its own is not a complete chord
    mods: list[str] = []
    masked = state & _MOD_MASK
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


class OverlayWindow(Gtk.ApplicationWindow):
    def __init__(self, app: "OverlayApplication") -> None:
        super().__init__(application=app)
        self.add_css_class("cachy-overlay")
        self.set_decorated(False)
        self.set_title("Keybindings")

        self.backend = detect.detect_active()
        self._all_backends = detect.detect_installed()
        if self.backend is None and self._all_backends:
            self.backend = self._all_backends[0]

        self.model = OverlayModel()
        self._app_suggestions: list[appscan.DesktopApp] = []
        self._suggestion_index = 0

        self._build_ui()
        self._apply_theme()
        self._setup_layer_shell()

        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

        self.connect("notify::visible", self._on_visibility_changed)

    # --- lifecycle -------------------------------------------------------

    def _reload(self) -> None:
        if self.backend is None:
            self.model.set_shortcuts([])
            return
        try:
            app_id = self.backend.focused_window()
        except Exception:  # noqa: BLE001 - focused-window is best-effort
            app_id = None
        self.model.app_context = app_id
        shortcuts = self.backend.read()
        shortcuts.extend(cheatsheets.load_for(app_id))
        self.model.set_shortcuts(shortcuts)

    def _setup_layer_shell(self) -> None:
        if not _HAS_LAYER_SHELL or not LayerShell.is_supported():
            # Plain centered window fallback (e.g. running nested, or a
            # compositor without wlr-layer-shell). Still fully usable.
            self.set_default_size(720, 560)
            return
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "cachy-shortcuts")
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        # Anchor to every edge so the (transparent) surface spans the output;
        # the visible panel is centered *within* it by widget alignment. This
        # is what makes the backdrop click-through-free without reserving any
        # screen space itself.
        for edge in (
            LayerShell.Edge.LEFT,
            LayerShell.Edge.RIGHT,
            LayerShell.Edge.TOP,
            LayerShell.Edge.BOTTOM,
        ):
            LayerShell.set_anchor(self, edge, True)
        # Never reserve space or push Noctalia/DMS bars around.
        LayerShell.set_exclusive_zone(self, -1)

    def _set_keyboard_exclusive(self, exclusive: bool) -> None:
        if not _HAS_LAYER_SHELL or not LayerShell.is_supported():
            return
        mode = (
            LayerShell.KeyboardMode.EXCLUSIVE
            if exclusive
            else LayerShell.KeyboardMode.NONE
        )
        LayerShell.set_keyboard_mode(self, mode)

    def _on_visibility_changed(self, *_args) -> None:
        if self.get_visible():
            self._reload()
            self.model.cancel()
            self.search_entry.set_text("")
            self._render()
            self._set_keyboard_exclusive(True)
            self.search_entry.grab_focus()
        else:
            self._set_keyboard_exclusive(False)

    def close_overlay(self) -> None:
        self.set_visible(False)

    # --- widget tree -------------------------------------------------------

    def _build_ui(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_halign(Gtk.Align.CENTER)
        outer.set_valign(Gtk.Align.CENTER)
        outer.set_hexpand(True)
        outer.set_vexpand(True)
        self.set_child(outer)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("panel")
        panel.set_size_request(680, 500)
        outer.append(panel)
        self.panel = panel

        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_row.set_margin_top(4)
        self.header_label = Gtk.Label(label=self.model.header())
        self.header_label.add_css_class("context")
        self.header_label.set_halign(Gtk.Align.START)
        header_row.append(self.header_label)
        panel.append(header_row)

        self.search_revealer = Gtk.Revealer()
        self.search_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN
        )
        self.search_entry = Gtk.Entry()
        self.search_entry.add_css_class("search")
        self.search_entry.set_placeholder_text("Type to search…")
        self.search_entry.connect("changed", self._on_search_changed)
        self.search_revealer.set_child(self.search_entry)
        panel.append(self.search_revealer)

        self.command_entry = Gtk.Entry()
        self.command_entry.add_css_class("search")
        self.command_entry.set_visible(False)
        self.command_entry.connect("changed", self._on_command_changed)
        panel.append(self.command_entry)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.set_child(self.list_box)
        panel.append(scroller)

        self.status_label = Gtk.Label(label="")
        self.status_label.add_css_class("status")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_wrap(True)
        self.status_label.set_visible(False)
        panel.append(self.status_label)

        self.hint_label = Gtk.Label(label=self.model.hint())
        self.hint_label.add_css_class("hint")
        self.hint_label.set_halign(Gtk.Align.START)
        panel.append(self.hint_label)

    def _apply_theme(self) -> None:
        palette = theming.current_palette(self.backend.name if self.backend else None)
        provider = Gtk.CssProvider()
        provider.load_from_string(stylesheet(palette))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    # --- rendering ---------------------------------------------------------

    def _render(self) -> None:
        self.header_label.set_label(self.model.header())
        self.hint_label.set_label(self.model.hint())

        conflict_mode = self.model.mode in (Mode.CAPTURE_CHORD, Mode.EDIT_COMMAND)
        has_conflict = conflict_mode and self.model.pending_chord is not None and (
            self.model.conflict_for(self.model.pending_chord) is not None
        )
        self.status_label.remove_css_class("conflict")
        if has_conflict:
            self.status_label.add_css_class("conflict")

        if self.model.status:
            self.status_label.set_label(self.model.status)
            self.status_label.set_visible(True)
        else:
            self.status_label.set_visible(False)

        self.search_revealer.set_reveal_child(bool(self.model.query))
        self.command_entry.set_visible(self.model.mode is Mode.EDIT_COMMAND)
        if self.model.mode is Mode.EDIT_COMMAND:
            if self.command_entry.get_text() != self.model.draft_command:
                self.command_entry.set_text(self.model.draft_command)

        self._clear_rows()
        for index, row in enumerate(self.model.rows):
            self.list_box.append(self._build_row(index, row))

    def _clear_rows(self) -> None:
        child = self.list_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.list_box.remove(child)
            child = nxt

    def _build_row(self, index: int, row) -> Gtk.ListBoxRow:
        list_row = Gtk.ListBoxRow()
        list_row.set_selectable(False)
        list_row.set_activatable(False)

        if row.kind is RowKind.SECTION:
            label = Gtk.Label(label=row.title)
            label.add_css_class("section")
            label.set_halign(Gtk.Align.START)
            list_row.set_child(label)
            return list_row

        shortcut = row.shortcut
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        box.add_css_class("row")
        if shortcut.extras.get("disabled"):
            box.add_css_class("disabled")
        if index == self.model.selected:
            box.add_css_class("selected")

        # While capturing, show the pending chord in place of the row being
        # rebound, so the live conflict feedback reads against what it will
        # actually become rather than what it currently is.
        display_chord = shortcut.chord.display()
        if (
            self.model.mode is Mode.CAPTURE_CHORD
            and self.model.pending_chord is not None
            and shortcut is self.model.editing_target
        ):
            display_chord = self.model.pending_chord.display()

        chord_label = Gtk.Label(label=display_chord)
        chord_label.add_css_class("chord")
        chord_label.set_halign(Gtk.Align.START)
        chord_label.set_size_request(220, -1)
        box.append(chord_label)

        arrow = Gtk.Label(label="→")
        arrow.add_css_class("desc")
        box.append(arrow)

        desc_label = Gtk.Label(label=shortcut.label)
        desc_label.add_css_class("desc")
        desc_label.set_halign(Gtk.Align.START)
        box.append(desc_label)

        list_row.set_child(box)
        return list_row

    # --- search ------------------------------------------------------------

    def _on_search_changed(self, entry: Gtk.Entry) -> None:
        if self.model.mode is not Mode.BROWSE:
            return
        self.model.set_query(entry.get_text())
        self._render()

    def _on_command_changed(self, entry: Gtk.Entry) -> None:
        if self.model.mode is not Mode.EDIT_COMMAND:
            return
        self.model.draft_command = entry.get_text()
        self._app_suggestions = []

    # --- input ---------------------------------------------------------

    def _on_key_pressed(self, controller, keyval, keycode, state) -> bool:
        name = Gdk.keyval_name(keyval) or ""
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

        if self.model.mode is Mode.CAPTURE_CHORD:
            return self._handle_capture_key(keyval, state, name)
        if self.model.mode is Mode.EDIT_COMMAND:
            return self._handle_command_key(keyval, state, name)
        if self.model.mode is Mode.CONFIRM_DELETE:
            return self._handle_delete_key(name)
        return self._handle_browse_key(name, ctrl)

    def _handle_browse_key(self, name: str, ctrl: bool) -> bool:
        if name == "Escape":
            self.close_overlay()
            return True
        if name == "Up":
            self.model.move(-1)
            self._render()
            return True
        if name == "Down":
            self.model.move(1)
            self._render()
            return True
        if name == "Return" and ctrl:
            if self.model.begin_command_edit():
                self._render()
            return True
        if name == "Return":
            if self.model.begin_capture():
                self._render()
            return True
        if ctrl and name.lower() == "n":
            if self.model.begin_add():
                self._render()
            return True
        if ctrl and name.lower() == "d":
            if self.model.begin_delete():
                self._render()
            return True
        # `n`/`d` are only mnemonics while the search box is empty: once a
        # query is in progress those letters are just letters (you can still
        # search for "nautilus" or "discord"), and Ctrl+N / Ctrl+D above
        # remain the reliable path regardless of query state.
        if not self.model.query:
            if name.lower() == "n":
                if self.model.begin_add():
                    self._render()
                return True
            if name.lower() == "d":
                if self.model.begin_delete():
                    self._render()
                return True
        return False  # let it fall through to the search entry

    def _handle_capture_key(self, keyval: int, state, name: str) -> bool:
        if name == "Escape":
            self.model.cancel()
            self._render()
            return True
        if name == "Return" and self.model.pending_chord is not None:
            self._commit_capture()
            return True
        chord = _chord_from_event(keyval, state)
        if chord is None:
            return True  # swallow bare modifier presses, keep waiting
        self.model.capture(chord)
        self._render()
        return True

    def _commit_capture(self) -> None:
        chord = self.model.pending_chord
        target = self.model.editing_target
        if chord is None or self.backend is None:
            self.model.cancel()
            self._render()
            return
        try:
            if target is None:
                # A brand-new binding needs a command next.
                self.model.mode = Mode.EDIT_COMMAND
                self.model.draft_command = ""
                self.model.status = "Type a command or pick an app…  Esc to cancel"
                self._app_suggestions = []
                self._render()
                self.command_entry.grab_focus()
                return
            editor.rebind(self.backend, target, chord)
        except editor.EditError as exc:
            self.model.status = str(exc)
            self._render()
            return
        self.model.cancel()
        self._reload()
        self._render()
        self.search_entry.grab_focus()

    def _handle_command_key(self, keyval: int, state, name: str) -> bool:
        if name == "Escape":
            self.model.cancel()
            self._app_suggestions = []
            self._render()
            self.search_entry.grab_focus()
            return True
        if name == "Tab":
            self._cycle_app_suggestion()
            return True
        if name == "Return":
            self._commit_command()
            return True
        return False  # let the command entry handle normal typing

    def _cycle_app_suggestion(self) -> None:
        query = self.command_entry.get_text().strip()
        if not self._app_suggestions:
            self._app_suggestions = appscan.search(query, limit=8) if query else []
            self._suggestion_index = 0
        if not self._app_suggestions:
            return
        app = self._app_suggestions[self._suggestion_index % len(self._app_suggestions)]
        self._suggestion_index += 1
        self.command_entry.set_text(app.command)
        self.command_entry.set_position(-1)
        if not self.model.draft_command:
            self.model.draft_command = app.command

    def _commit_command(self) -> None:
        command = self.command_entry.get_text().strip()
        if not command or self.backend is None:
            self.model.cancel()
            self._render()
            return
        chord = self.model.pending_chord
        target = self.model.editing_target
        action = editor.wrap_command_as_action(self.backend, command)
        try:
            if target is None and chord is not None:
                editor.add(self.backend, chord, action)
            elif target is not None:
                editor.retarget(self.backend, target, action)
        except editor.EditError as exc:
            self.model.status = str(exc)
            self._render()
            return
        self.model.cancel()
        self._app_suggestions = []
        self._reload()
        self._render()
        self.search_entry.grab_focus()

    def _handle_delete_key(self, name: str) -> bool:
        if name == "Escape":
            self.model.cancel()
            self._render()
            return True
        if name.lower() == "y":
            target = self.model.editing_target
            if target is not None and self.backend is not None:
                try:
                    editor.delete(self.backend, target)
                except editor.EditError as exc:
                    self.model.status = str(exc)
                    self._render()
                    return True
            self.model.cancel()
            self._reload()
            self._render()
            self.search_entry.grab_focus()
            return True
        return True  # swallow everything else while confirming


class OverlayApplication(Gtk.Application):
    def __init__(self, toggle: bool) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self._toggle = toggle
        self.window: OverlayWindow | None = None

    def do_activate(self) -> None:  # noqa: N802 - GObject virtual method name
        if self.window is None:
            self.window = OverlayWindow(self)
        if self._toggle and self.window.get_visible():
            self.window.close_overlay()
        else:
            self.window.present()
            self.window.set_visible(True)


def run(toggle: bool = True) -> int:
    app = OverlayApplication(toggle=toggle)
    return app.run(None)
