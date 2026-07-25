"""Stylesheet for the overlay, generated from the active palette.

Built in Python rather than shipped as a .css file because every colour is
substituted from whatever Noctalia or DMS is currently themed with -- run
through ``theming.ensure_contrast`` first, so a shell's palette can shift the
hues without dragging the text down to unreadable.

Two rules the design leans on:

* **The panel is opaque and the screen behind it is dimmed.** An overlay that
  lets the desktop show through its text is unreadable no matter what the
  colours are, and a scrim also tells you the overlay has the keyboard.
* **Selection is a filled highlight, not a colour swap.** Recolouring the text
  to the accent moves it *away* from maximum contrast; a background fill plus
  an accent bar makes the current row obvious while leaving the words at full
  strength.
"""

from __future__ import annotations

from ..theming import Palette


def stylesheet(palette: Palette) -> str:
    return f"""
/* The layer-shell surface spans the whole output; this is the dim behind the
   panel, which is also the visual cue that the overlay owns the keyboard. */
window.cachy-overlay {{
    background-color: rgba(0, 0, 0, 0.55);
}}

.panel {{
    background-color: {palette.background};
    border: 1px solid alpha({palette.muted}, 0.35);
    border-radius: 12px;
    box-shadow: 0 24px 64px rgba(0, 0, 0, 0.55);
    padding: 0;
}}

/* GTK's stock list chrome fights a flat panel: drop it and style our own
   boxes instead, so a row's highlight is ours and not Adwaita's. */
list, list > row {{
    background: transparent;
    border: none;
    padding: 0;
}}

scrolledwindow, viewport {{
    background: transparent;
}}

/* --- header ---------------------------------------------------------- */

.context {{
    font-family: monospace;
    font-size: 10pt;
    font-weight: bold;
    color: {palette.text};
    padding: 16px 24px 4px 24px;
}}

.context-app {{
    color: {palette.accent};
}}

.count {{
    font-family: monospace;
    font-size: 9.5pt;
    color: {palette.muted};
    padding: 16px 24px 4px 24px;
}}

/* --- search ---------------------------------------------------------- */

.search {{
    font-family: monospace;
    font-size: 12pt;
    color: {palette.text};
    background-color: alpha({palette.surface}, 0.7);
    border: none;
    border-bottom: 1px solid alpha({palette.muted}, 0.3);
    border-radius: 0;
    padding: 13px 24px;
    caret-color: {palette.accent};
    box-shadow: none;
    outline: none;
}}

.search:focus, .search:focus-within {{
    border: none;
    border-bottom: 1px solid {palette.accent};
    box-shadow: none;
    outline: none;
}}

.search placeholder, .search text placeholder {{
    color: {palette.muted};
}}

/* --- list ------------------------------------------------------------ */

.section {{
    font-family: monospace;
    font-size: 9pt;
    font-weight: bold;
    color: {palette.muted};
    text-transform: uppercase;
    letter-spacing: 1px;
    padding: 18px 24px 6px 24px;
}}

.row {{
    padding: 7px 24px;
    border-left: 2px solid transparent;
    background: transparent;
}}

/* A filled highlight rather than a colour swap -- see the module docstring. */
.row.selected {{
    background-color: alpha({palette.accent}, 0.16);
    border-left: 2px solid {palette.accent};
}}

.chord {{
    font-family: monospace;
    font-size: 11.5pt;
    font-weight: bold;
    color: {palette.text};
    background-color: alpha({palette.surface}, 0.9);
    border: 1px solid alpha({palette.muted}, 0.4);
    border-radius: 5px;
    padding: 2px 9px;
}}

.row.selected .chord {{
    border-color: alpha({palette.accent}, 0.6);
}}

.arrow {{
    font-family: monospace;
    font-size: 11pt;
    color: {palette.muted};
}}

.desc {{
    font-family: monospace;
    font-size: 11.5pt;
    color: {palette.text_dim};
}}

.row.selected .desc {{
    color: {palette.text};
}}

.row.disabled .chord,
.row.disabled .desc,
.row.disabled .arrow {{
    color: {palette.muted};
}}

.row.conflict .chord {{
    color: {palette.warning};
    border-color: {palette.warning};
}}

.row.conflict .desc,
.row.conflict .arrow {{
    color: {palette.warning};
}}

/* Reference rows from an app cheat sheet aren't editable here; the badge says
   so up front instead of the edit flow refusing after the fact. */
.badge {{
    font-family: monospace;
    font-size: 8.5pt;
    color: {palette.muted};
    border: 1px solid alpha({palette.muted}, 0.45);
    border-radius: 4px;
    padding: 0 6px;
}}

.empty {{
    font-family: monospace;
    font-size: 11pt;
    color: {palette.muted};
    padding: 28px 24px;
}}

.create-row {{
    padding: 7px 24px;
    border-left: 2px solid transparent;
}}

.create-row.selected {{
    background-color: alpha({palette.accent}, 0.16);
    border-left: 2px solid {palette.accent};
}}

.create-label {{
    font-family: monospace;
    font-size: 11.5pt;
    color: {palette.accent};
}}

/* --- binding form ---------------------------------------------------- */

.form {{
    padding: 6px 24px 14px 24px;
}}

.form-title {{
    font-family: monospace;
    font-size: 10pt;
    font-weight: bold;
    color: {palette.text};
    text-transform: uppercase;
    letter-spacing: 1px;
    padding: 18px 0 14px 0;
}}

.field-label {{
    font-family: monospace;
    font-size: 9.5pt;
    color: {palette.muted};
    padding: 0 0 4px 0;
}}

.field {{
    font-family: monospace;
    font-size: 11.5pt;
    color: {palette.text};
    background-color: alpha({palette.surface}, 0.85);
    border: 1px solid alpha({palette.muted}, 0.4);
    border-radius: 6px;
    padding: 9px 12px;
    caret-color: {palette.accent};
    box-shadow: none;
    outline: none;
}}

.field:focus, .field:focus-within {{
    border: 1px solid {palette.accent};
    box-shadow: none;
    outline: none;
}}

.field placeholder, .field text placeholder {{
    color: {palette.muted};
}}

/* The chord field is a label, not an entry: it takes key presses, not text.
   The dashed border while armed is what says "press something now". */
.chordfield {{
    font-family: monospace;
    font-size: 11.5pt;
    font-weight: bold;
    color: {palette.text};
    background-color: alpha({palette.surface}, 0.85);
    border: 1px solid alpha({palette.muted}, 0.4);
    border-radius: 6px;
    padding: 9px 12px;
}}

.chordfield.armed {{
    border: 1px dashed {palette.accent};
    color: {palette.accent};
}}

.chordfield.conflict {{
    border: 1px solid {palette.warning};
    color: {palette.warning};
}}

.chordfield.empty {{
    color: {palette.muted};
    font-weight: normal;
    padding: 9px 12px;
}}

.suggestion {{
    font-family: monospace;
    font-size: 11pt;
    color: {palette.text_dim};
    padding: 5px 12px;
    border-left: 2px solid transparent;
    border-radius: 4px;
}}

.suggestion.selected {{
    background-color: alpha({palette.accent}, 0.16);
    border-left: 2px solid {palette.accent};
    color: {palette.text};
}}

.suggestion-command {{
    font-family: monospace;
    font-size: 9.5pt;
    color: {palette.muted};
}}

.note {{
    font-family: monospace;
    font-size: 9.5pt;
    color: {palette.muted};
    padding: 6px 0 0 0;
}}

.note.warning {{
    color: {palette.warning};
}}

/* --- footer ---------------------------------------------------------- */

.hint {{
    font-family: monospace;
    font-size: 9.5pt;
    color: {palette.muted};
    padding: 12px 24px;
    border-top: 1px solid alpha({palette.muted}, 0.25);
}}

.status {{
    font-family: monospace;
    font-size: 10.5pt;
    color: {palette.text};
    background-color: alpha({palette.accent}, 0.12);
    padding: 12px 24px;
    border-top: 1px solid alpha({palette.accent}, 0.45);
}}

.status.conflict {{
    color: {palette.warning};
    background-color: alpha({palette.warning}, 0.12);
    border-top: 1px solid alpha({palette.warning}, 0.55);
}}

/* --- scrollbar ------------------------------------------------------- */

scrollbar {{
    background: transparent;
    border: none;
}}

scrollbar slider {{
    background-color: alpha({palette.muted}, 0.45);
    border-radius: 6px;
    min-width: 6px;
}}

scrollbar slider:hover {{
    background-color: alpha({palette.muted}, 0.7);
}}
"""
