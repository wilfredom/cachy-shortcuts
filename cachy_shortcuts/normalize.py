"""Canonicalisation of modifier and key spellings across compositor dialects.

This module is the load-bearing piece of the whole tool. Niri writes
``Mod+Shift+Slash``, COSMIC writes ``(modifiers: [Super, Shift], key: "slash")``
and Mango writes ``SUPER+SHIFT,slash`` -- all three mean the same physical
chord. If they don't collapse to one canonical form then conflict detection
silently passes and search silently misses, so the translation tables below are
deliberately explicit rather than clever.
"""

from __future__ import annotations

# Canonical modifier names, in the order they are displayed.
MOD_ORDER: tuple[str, ...] = ("super", "ctrl", "alt", "shift")

# Every spelling any of the three backends (or a human) might use.
#
# Note on niri's "Mod": it resolves to Super when niri runs on a TTY and to Alt
# when nested inside another compositor. Super is the overwhelmingly common
# case and the only one that matters for a config file on disk, so Mod -> super.
_MOD_ALIASES: dict[str, str] = {
    "super": "super",
    "mod": "super",
    "mod4": "super",
    "win": "super",
    "windows": "super",
    "logo": "super",
    "meta": "super",
    "super_l": "super",
    "super_r": "super",
    "ctrl": "ctrl",
    "control": "ctrl",
    "ctl": "ctrl",
    "control_l": "ctrl",
    "control_r": "ctrl",
    "alt": "alt",
    "mod1": "alt",
    "option": "alt",
    "alt_l": "alt",
    "alt_r": "alt",
    "shift": "shift",
    "shift_l": "shift",
    "shift_r": "shift",
    # X11 modifier slots Hyprland lets a bind name directly. They have no
    # canonical name of their own, so they stay themselves and sort after the
    # four every compositor shares. Dropping them instead would be worse than
    # ugly: two different chords would compare equal.
    "mod2": "mod2",
    "mod3": "mod3",
    "mod5": "mod5",
    "caps": "caps",
    "capslock": "caps",
    "caps_lock": "caps",
}

# Mango uses NONE as an explicit "no modifiers" marker.
_MOD_IGNORED = {"none", ""}

_KEY_ALIASES: dict[str, str] = {
    # Enter / escape
    "return": "return",
    "enter": "return",
    "kp_enter": "return",
    "kpenter": "return",
    "escape": "escape",
    "esc": "escape",
    # Whitespace-ish
    "space": "space",
    " ": "space",
    "tab": "tab",
    "backspace": "backspace",
    "bksp": "backspace",
    "delete": "delete",
    "del": "delete",
    "insert": "insert",
    "ins": "insert",
    # Punctuation, symbol form and name form both accepted
    "slash": "slash",
    "/": "slash",
    "backslash": "backslash",
    "\\": "backslash",
    "bracketleft": "bracketleft",
    "[": "bracketleft",
    "bracketright": "bracketright",
    "]": "bracketright",
    "minus": "minus",
    "-": "minus",
    "equal": "equal",
    "=": "equal",
    "comma": "comma",
    ",": "comma",
    "period": "period",
    "dot": "period",
    ".": "period",
    "semicolon": "semicolon",
    ";": "semicolon",
    "apostrophe": "apostrophe",
    "quote": "apostrophe",
    "'": "apostrophe",
    "grave": "grave",
    "backtick": "grave",
    "`": "grave",
    # Navigation
    "home": "home",
    "end": "end",
    "page_up": "page_up",
    "pageup": "page_up",
    "prior": "page_up",
    "page_down": "page_down",
    "pagedown": "page_down",
    "next": "page_down",
    "up": "up",
    "arrowup": "up",
    "down": "down",
    "arrowdown": "down",
    "left": "left",
    "arrowleft": "left",
    "right": "right",
    "arrowright": "right",
    # Misc
    "print": "print",
    "printscreen": "print",
    "print_screen": "print",
    "sysreq": "print",
}

# How canonical keys render in the overlay. Anything not listed falls back to
# uppercasing, which is right for letters, digits and F-keys.
_KEY_DISPLAY: dict[str, str] = {
    "slash": "/",
    "backslash": "\\",
    "bracketleft": "[",
    "bracketright": "]",
    "minus": "-",
    "equal": "=",
    "comma": ",",
    "period": ".",
    "semicolon": ";",
    "apostrophe": "'",
    "grave": "`",
    "page_up": "PGUP",
    "page_down": "PGDN",
}

# Friendly names for the XF86 media keys that show up in every real config.
_XF86_DISPLAY: dict[str, str] = {
    "xf86audioraisevolume": "VOL UP",
    "xf86audiolowervolume": "VOL DOWN",
    "xf86audiomute": "MUTE",
    "xf86audiomicmute": "MIC MUTE",
    "xf86audioplay": "PLAY",
    "xf86audiopause": "PAUSE",
    "xf86audionext": "NEXT TRACK",
    "xf86audioprev": "PREV TRACK",
    "xf86monbrightnessup": "BRIGHT UP",
    "xf86monbrightnessdown": "BRIGHT DOWN",
}


def normalize_mod(raw: str) -> str | None:
    """Canonicalise one modifier. Returns None for 'no modifier' markers.

    Raises KeyError for genuinely unknown modifiers so that a typo in a config
    surfaces as a parse warning rather than a silently dropped modifier -- a
    dropped modifier would make two different chords compare equal.
    """
    token = raw.strip().lower()
    if token in _MOD_IGNORED:
        return None
    # Mango allows raw evdev codes in the modifier position.
    if token.startswith("code:"):
        return token
    return _MOD_ALIASES[token]


def normalize_key(raw: str) -> str:
    """Canonicalise a key name to lowercase, alias-resolved form."""
    token = raw.strip()
    if not token:
        raise ValueError("empty key")
    # Raw evdev keycodes (Mango) cannot be resolved without an xkb keymap.
    # Keep them opaque but comparable rather than guessing wrong.
    if token.lower().startswith("code:"):
        return token.lower()
    lowered = token.lower()
    if lowered in _KEY_ALIASES:
        return _KEY_ALIASES[lowered]
    return lowered


def order_mods(mods: set[str]) -> tuple[str, ...]:
    """Sort canonical modifiers into display order, unknowns last."""
    known = [m for m in MOD_ORDER if m in mods]
    extra = sorted(m for m in mods if m not in MOD_ORDER)
    return tuple(known + extra)


def display_key(key: str) -> str:
    """Render a canonical key the way it appears in the overlay."""
    if key in _KEY_DISPLAY:
        return _KEY_DISPLAY[key]
    if key in _XF86_DISPLAY:
        return _XF86_DISPLAY[key]
    if key.startswith("code:"):
        return key.upper()
    if key.startswith("xf86"):
        # Unknown media key: strip the prefix so it at least reads as words.
        return key[4:].upper()
    return key.upper()
