"""Stylesheet for the overlay, generated from the active palette.

Built in Python rather than shipped as a .css file because every colour is
substituted from whatever Noctalia or DMS is currently themed with.
"""

from __future__ import annotations

from ..theming import Palette


def stylesheet(palette: Palette) -> str:
    return f"""
window.cachy-overlay {{
    background: transparent;
}}

.panel {{
    background-color: alpha({palette.background}, 0.94);
    border: 1px solid {palette.accent};
    border-radius: 4px;
    padding: 0;
}}

.search {{
    font-family: monospace;
    font-size: 12pt;
    color: {palette.text};
    background-color: alpha({palette.surface}, 0.55);
    border: none;
    border-bottom: 1px solid alpha({palette.muted}, 0.25);
    border-radius: 0;
    padding: 14px 22px;
    caret-color: {palette.accent};
    box-shadow: none;
    outline: none;
}}

.search:focus {{
    border: none;
    border-bottom: 1px solid alpha({palette.accent}, 0.55);
    box-shadow: none;
    outline: none;
}}

.search placeholder {{
    color: {palette.muted};
}}

.context {{
    font-family: monospace;
    font-size: 9pt;
    color: {palette.accent};
    padding: 0 22px;
}}

.section {{
    font-family: monospace;
    font-size: 9pt;
    color: {palette.muted};
    padding: 18px 22px 6px 22px;
    letter-spacing: 1px;
}}

.row {{
    padding: 6px 22px;
    background: transparent;
}}

/* Selection is signalled by colour alone, as in the reference -- a background
   tint reads as heavier chrome and fights the flat, quiet look. */
.row.selected {{
    background: transparent;
}}

.chord {{
    font-family: monospace;
    font-size: 11pt;
    color: {palette.text};
}}

.desc {{
    font-family: monospace;
    font-size: 11pt;
    color: {palette.text_dim};
}}

.row.selected .chord,
.row.selected .desc {{
    color: {palette.accent};
}}

.row.disabled .chord,
.row.disabled .desc {{
    color: {palette.muted};
}}

.row.conflict .chord,
.row.conflict .desc {{
    color: {palette.warning};
}}

.hint {{
    font-family: monospace;
    font-size: 9pt;
    color: {palette.muted};
    padding: 12px 22px;
    border-top: 1px solid alpha({palette.muted}, 0.2);
}}

.status {{
    font-family: monospace;
    font-size: 10pt;
    color: {palette.accent};
    padding: 12px 22px;
    border-top: 1px solid alpha({palette.accent}, 0.35);
}}

.status.conflict {{
    color: {palette.warning};
    border-top: 1px solid alpha({palette.warning}, 0.5);
}}

scrollbar {{
    background: transparent;
    border: none;
}}

scrollbar slider {{
    background-color: alpha({palette.muted}, 0.35);
    border-radius: 6px;
    min-width: 5px;
}}
"""
