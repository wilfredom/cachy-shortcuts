"""cachy-shortcuts: an editable keybinding atlas for COSMIC, Niri, Hyprland and MangoWM."""

__version__ = "0.1.0"

# The overlay's GTK application id, and therefore its Wayland ``app_id`` -- but
# only because ``ui.overlay`` explicitly sets the program name to match. GTK
# derives app_id from ``g_get_prgname()``, which otherwise ends up as the
# executable name, so both spellings are what a window rule has to match.
APP_ID = "dev.cachyos.Shortcuts"
APP_IDS = (APP_ID, "cachy-shortcuts")

# The overlay window's title, which COSMIC's tiling exceptions match on.
WINDOW_TITLE = "Keybindings"

# Lives in every generated float rule so installing one is idempotent and a
# hand-editing user can see where it came from.
RULE_MARKER = "cachy-shortcuts"
