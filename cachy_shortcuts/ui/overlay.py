"""GTK4 layer-shell overlay -- the visible half of the tool.

All decision logic (filtering, grouping, selection, the edit form) lives in
``viewmodel.py`` and ``form_model.py`` and is unit-tested there without GTK.
This module is deliberately thin: it turns GDK key events into model calls and
paints widgets from the result.

Honest limitation: this file cannot be run or visually verified in the
container that built it (no Wayland session, no GTK4/gtk4-layer-shell
installed here). It is written against the documented gtk4-layer-shell API and
kept as thin as possible over the already-tested models, so that the surface
which *can't* be verified by execution is as small as it can be. Verify on the
real machine with ``cachy-shortcuts doctor`` first (it reports whether
layer-shell actually loaded) and then ``cachy-shortcuts overlay``.
"""

from __future__ import annotations

# Must come first: it loads libgtk4-layer-shell before GTK pulls in
# libwayland-client, which is the only order in which layer-shell works. See
# that module's docstring.
from . import _layershell
from ._layershell import Gdk, Gio, GLib, Gtk, LayerShell

from .. import APP_ID, WINDOW_TITLE, appscan, cheatsheets, detect, editor, theming
from .binding_form import BindingForm
from .style import stylesheet
from .viewmodel import Mode, OverlayModel, RowKind

# Fractions of the monitor the panel takes, and the size it falls back to when
# the monitor's geometry can't be read.
_PANEL_WIDTH_FRACTION = 0.78
_PANEL_HEIGHT_FRACTION = 0.76
_PANEL_MAX_WIDTH = 920
_PANEL_MIN = (640, 460)

# How much of the list to keep visible past the selected row when scrolling.
_SCROLL_MARGIN = 32


class OverlayWindow(Gtk.ApplicationWindow):
    def __init__(self, app: "OverlayApplication") -> None:
        super().__init__(application=app)
        self.add_css_class("cachy-overlay")
        self.set_decorated(False)
        self.set_title(WINDOW_TITLE)

        self.backend = detect.detect_active()
        self._all_backends = detect.detect_installed()
        if self.backend is None and self._all_backends:
            self.backend = self._all_backends[0]

        self.model = OverlayModel()
        self._row_widgets: dict[int, Gtk.Widget] = {}
        self._scroll_pending = False

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
        if not _layershell.available():
            # No layer-shell: an ordinary toplevel, which a tiling compositor
            # will lay out unless `cachy-shortcuts install-rules` has added a
            # float rule. Going fullscreen means that even then it covers the
            # screen rather than landing in a column.
            self.set_default_size(*_PANEL_MIN)
            self.fullscreen()
            return
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "cachy-shortcuts")
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        # Anchor to every edge so the surface spans the output; the visible
        # panel is centered *within* it by widget alignment, and the rest is
        # the dimming scrim.
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
        if not _layershell.available():
            return
        mode = (
            LayerShell.KeyboardMode.EXCLUSIVE
            if exclusive
            else LayerShell.KeyboardMode.NONE
        )
        LayerShell.set_keyboard_mode(self, mode)

    def _on_visibility_changed(self, *_args) -> None:
        if self.get_visible():
            # Scanning .desktop files touches every applications directory, so
            # it happens once per opening and the form re-ranks the cache.
            self.model.apps = appscan.scan()
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
        panel.set_size_request(*self._panel_size())
        outer.append(panel)
        self.panel = panel

        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.header_label = Gtk.Label(label=self.model.header(), xalign=0)
        self.header_label.add_css_class("context")
        self.header_label.set_hexpand(True)
        header_row.append(self.header_label)
        self.count_label = Gtk.Label(label="", xalign=1)
        self.count_label.add_css_class("count")
        header_row.append(self.count_label)
        panel.append(header_row)

        self.search_entry = Gtk.Entry()
        self.search_entry.add_css_class("search")
        self.search_entry.set_placeholder_text("Type to search…")
        self.search_entry.connect("changed", self._on_search_changed)
        panel.append(self.search_entry)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_vexpand(True)
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.scroller.set_child(self.list_box)
        panel.append(self.scroller)

        # The form replaces the list while it is open, rather than sitting
        # alongside it: one screen at a time is what makes the keyboard rules
        # unambiguous.
        self.form = BindingForm(on_save=self._save_draft, on_cancel=self._cancel_form)
        self.form.set_vexpand(True)
        self.form.set_visible(False)
        panel.append(self.form)

        self.status_label = Gtk.Label(label="", xalign=0)
        self.status_label.add_css_class("status")
        self.status_label.set_wrap(True)
        self.status_label.set_visible(False)
        panel.append(self.status_label)

        self.hint_label = Gtk.Label(label=self.model.hint(), xalign=0)
        self.hint_label.add_css_class("hint")
        panel.append(self.hint_label)

    def _panel_size(self) -> tuple[int, int]:
        """Size the panel to the monitor rather than to a guess."""
        width, height = _PANEL_MIN
        display = Gdk.Display.get_default()
        if display is None:
            return (width, height)
        try:
            monitors = display.get_monitors()
            monitor = monitors.get_item(0) if monitors.get_n_items() else None
            if monitor is None:
                return (width, height)
            area = monitor.get_geometry()
        except Exception:  # noqa: BLE001 - a headless display must not crash us
            return (width, height)
        width = min(_PANEL_MAX_WIDTH, int(area.width * _PANEL_WIDTH_FRACTION))
        height = int(area.height * _PANEL_HEIGHT_FRACTION)
        return (max(width, _PANEL_MIN[0]), max(height, _PANEL_MIN[1]))

    def _apply_theme(self) -> None:
        palette = theming.current_palette(self.backend.name if self.backend else None)
        provider = Gtk.CssProvider()
        css = stylesheet(palette)
        # load_from_string arrived in GTK 4.12; load_from_data is the older
        # spelling and still present.
        if hasattr(provider, "load_from_string"):
            provider.load_from_string(css)
        else:
            provider.load_from_data(css.encode("utf-8"))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    # --- rendering ---------------------------------------------------------

    def _render(self) -> None:
        """Full repaint: use after the data or the mode changed."""
        in_form = self.model.mode is Mode.FORM
        self.header_label.set_label(self.model.header())
        self.hint_label.set_label(self.model.hint())

        self.search_entry.set_visible(not in_form)
        self.scroller.set_visible(not in_form)
        self.form.set_visible(in_form)
        self.count_label.set_label("" if in_form else self._count_text())

        if self.model.status:
            self.status_label.set_label(self.model.status)
            self.status_label.set_visible(True)
        else:
            self.status_label.set_visible(False)
        wants_warning = self.model.mode is Mode.CONFIRM_DELETE
        if wants_warning and not self.status_label.has_css_class("conflict"):
            self.status_label.add_css_class("conflict")
        elif not wants_warning and self.status_label.has_css_class("conflict"):
            self.status_label.remove_css_class("conflict")

        if in_form:
            self.form.open(self.model.draft)
        else:
            self._rebuild_rows()

    def _count_text(self) -> str:
        total = self.model.count()
        return f"{total} binding" + ("" if total == 1 else "s")

    def _rebuild_rows(self) -> None:
        child = self.list_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.list_box.remove(child)
            child = nxt
        self._row_widgets = {}

        if not self.model.rows:
            empty = Gtk.Label(label="Nothing bound here yet.", xalign=0)
            empty.add_css_class("empty")
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            row.set_activatable(False)
            row.set_child(empty)
            self.list_box.append(row)
            return

        for index, row in enumerate(self.model.rows):
            self.list_box.append(self._build_row(index, row))
        self._refresh_selection()

    def _build_row(self, index: int, row) -> Gtk.ListBoxRow:
        list_row = Gtk.ListBoxRow()
        list_row.set_selectable(False)
        list_row.set_activatable(False)

        if row.kind is RowKind.SECTION:
            label = Gtk.Label(label=row.title, xalign=0)
            label.add_css_class("section")
            list_row.set_child(label)
            return list_row

        if row.kind is RowKind.CREATE:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            box.add_css_class("create-row")
            label = Gtk.Label(label=f"＋  Bind “{row.title}”…", xalign=0)
            label.add_css_class("create-label")
            box.append(label)
            list_row.set_child(box)
            self._row_widgets[index] = box
            return list_row

        shortcut = row.shortcut
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        box.add_css_class("row")
        if shortcut.extras.get("disabled"):
            box.add_css_class("disabled")

        chord_label = Gtk.Label(label=shortcut.chord.display(), xalign=0)
        chord_label.add_css_class("chord")
        chord_label.set_size_request(200, -1)
        box.append(chord_label)

        arrow = Gtk.Label(label="→")
        arrow.add_css_class("arrow")
        box.append(arrow)

        desc_label = Gtk.Label(label=shortcut.label, xalign=0)
        desc_label.add_css_class("desc")
        desc_label.set_hexpand(True)
        box.append(desc_label)

        # No source span means an app cheat-sheet entry, which this tool does
        # not own. Saying so up front beats refusing after an edit is typed.
        if shortcut.source is None:
            badge = Gtk.Label(label="reference")
            badge.add_css_class("badge")
            box.append(badge)

        list_row.set_child(box)
        self._row_widgets[index] = box
        return list_row

    def _refresh_selection(self) -> None:
        """Selection-only repaint: no teardown, so arrow keys stay cheap."""
        for index, widget in self._row_widgets.items():
            wanted = index == self.model.selected
            if wanted and not widget.has_css_class("selected"):
                widget.add_css_class("selected")
            elif not wanted and widget.has_css_class("selected"):
                widget.remove_css_class("selected")
        self._scroll_selected_into_view()

    def _scroll_selected_into_view(self) -> None:
        """Keep the selected row inside the viewport.

        A GtkListBox in NONE selection mode scrolls for the wheel and for
        nothing else, so moving the selection with the keyboard has to move
        the adjustment by hand. Deferred to an idle callback because a row
        added in this same frame has no allocation to measure yet.
        """
        if self._scroll_pending:
            return
        self._scroll_pending = True

        def run() -> bool:
            self._scroll_pending = False
            widget = self._row_widgets.get(self.model.selected)
            if widget is None:
                return False
            ok, bounds = widget.compute_bounds(self.list_box)
            if not ok:
                return False
            adjustment = self.scroller.get_vadjustment()
            page = adjustment.get_page_size()
            if page <= 0:
                return False
            value = adjustment.get_value()
            top = bounds.origin.y - _SCROLL_MARGIN
            bottom = bounds.origin.y + bounds.size.height + _SCROLL_MARGIN
            if top < value:
                adjustment.set_value(max(0.0, top))
            elif bottom > value + page:
                upper = max(0.0, adjustment.get_upper() - page)
                adjustment.set_value(min(upper, bottom - page))
            return False

        GLib.idle_add(run)

    # --- search ------------------------------------------------------------

    def _on_search_changed(self, entry: Gtk.Entry) -> None:
        if self.model.mode is not Mode.BROWSE:
            return
        self.model.set_query(entry.get_text())
        self.count_label.set_label(self._count_text())
        self._rebuild_rows()

    # --- input ---------------------------------------------------------

    def _on_key_pressed(self, controller, keyval, keycode, state) -> bool:
        name = Gdk.keyval_name(keyval) or ""
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

        if self.model.mode is Mode.FORM:
            return self.form.handle_key(keyval, state, name, ctrl)
        if self.model.mode is Mode.CONFIRM_DELETE:
            return self._handle_delete_key(name)
        return self._handle_browse_key(name, ctrl)

    def _handle_browse_key(self, name: str, ctrl: bool) -> bool:
        if name == "Escape":
            # Esc backs out of a search before it closes the overlay, so a
            # mistyped query doesn't cost you the whole window.
            if self.search_entry.get_text():
                self.search_entry.set_text("")
                return True
            self.close_overlay()
            return True
        if name in ("Up", "Down", "Page_Up", "Page_Down"):
            self._move_selection(name)
            return True
        if name in ("Home", "End"):
            # Only jump the list when there's no query: with text in the search
            # box these belong to the cursor, and stealing them there would be
            # rude in a way PgUp/PgDn never is.
            if self.search_entry.get_text():
                return False
            self._move_selection(name)
            return True
        if name in ("Return", "KP_Enter"):
            if self.model.activate(unwrap=self._unwrap):
                self._render()
            else:
                self._render()  # a status may have been set (reference rows)
            return True
        if ctrl and name.lower() == "n":
            self.model.begin_add()
            self._render()
            return True
        if ctrl and name.lower() == "d":
            self.model.begin_delete()
            self._render()
            return True
        return False  # let it fall through to the search entry

    def _move_selection(self, name: str) -> None:
        if name == "Up":
            self.model.move(-1)
        elif name == "Down":
            self.model.move(1)
        elif name == "Page_Up":
            self.model.move_page(-1)
        elif name == "Page_Down":
            self.model.move_page(1)
        elif name == "Home":
            self.model.move_to_edge(-1)
        else:
            self.model.move_to_edge(1)
        self._refresh_selection()

    def _unwrap(self, action: str) -> str | None:
        if self.backend is None:
            return None
        return editor.unwrap_action(self.backend, action)

    # --- committing --------------------------------------------------------

    def _cancel_form(self) -> None:
        self.model.cancel()
        self._render()
        self.search_entry.grab_focus()

    def _save_draft(self, draft) -> None:
        if self.backend is None:
            self.model.status = "No compositor config to write to."
            self._render()
            return
        action = (
            editor.wrap_command_as_action(self.backend, draft.command)
            if draft.spawns
            else draft.command.strip()
        )
        victim = draft.claimant() if draft.replace_confirmed else None
        try:
            if victim is not None:
                editor.take_over(
                    self.backend,
                    victim,
                    draft.target,
                    draft.chord,
                    action,
                    draft.description,
                )
            elif draft.target is None:
                editor.add(self.backend, draft.chord, action, draft.description)
            else:
                editor.update(
                    self.backend,
                    draft.target,
                    chord=draft.chord,
                    action=action,
                    description=draft.description,
                )
        except editor.EditError as exc:
            self.model.status = str(exc)
            self.form.refresh()
            self.status_label.set_label(self.model.status)
            self.status_label.set_visible(True)
            return
        self.model.cancel()
        self._reload()
        self._render()
        self.search_entry.grab_focus()

    def _handle_delete_key(self, name: str) -> bool:
        if name == "Escape":
            self._cancel_form()
            return True
        if name.lower() == "y":
            target = self.model.delete_target
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
        # GTK takes the Wayland app_id from the program name, not from the
        # application id, so without this the compositor sees "cachy-shortcuts"
        # and every window rule matching dev.cachyos.Shortcuts misses.
        GLib.set_prgname(APP_ID)
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


# Re-exported for callers that only want to know whether the overlay can be a
# real overlay; `cachy-shortcuts doctor` reports it.
layer_shell_status = _layershell.status
