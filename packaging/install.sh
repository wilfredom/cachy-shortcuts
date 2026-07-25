#!/usr/bin/env bash
# Convenience installer for anyone not going through the PKGBUILD (e.g.
# non-Arch, or just testing). It does exactly what the PKGBUILD's
# build()/package() do via pip instead of makepkg, then wires up the app's
# own hotkey the same way `cachy-shortcuts install-hotkey` always does.
#
# Safe to re-run: pip install is idempotent, the .desktop copy overwrites
# itself, and install-hotkey is a no-op if the hotkey is already bound.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 not found" >&2
  exit 1
fi

if ! python3 -m pip --version >/dev/null 2>&1; then
  echo "error: pip not found for python3." >&2
  echo "On CachyOS/Arch, prefer the PKGBUILD instead: makepkg -si" >&2
  echo "(or install pip first: sudo pacman -S python-pip)" >&2
  exit 1
fi

missing_system_deps=()
python3 - <<'PY' || missing_system_deps+=("python-gobject / gtk4")
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: F401
PY
python3 - <<'PY' || missing_system_deps+=("gtk4-layer-shell")
import gi
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk4LayerShell  # noqa: F401
PY

if [ "${#missing_system_deps[@]}" -gt 0 ]; then
  echo "warning: the overlay needs system packages this script cannot install:"
  for dep in "${missing_system_deps[@]}"; do
    echo "  - $dep"
  done
  echo "On CachyOS/Arch:  sudo pacman -S python-gobject gtk4 gtk4-layer-shell"
  echo "Continuing so the CLI (list/doctor/add/rm/...) is still usable."
  echo
fi

echo "Installing cachy-shortcuts..."
python3 -m pip install --user --upgrade .

desktop_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$desktop_dir"
cp "$repo_root/packaging/cachy-shortcuts.desktop" "$desktop_dir/"
echo "Installed launcher entry to $desktop_dir/cachy-shortcuts.desktop"

if ! command -v cachy-shortcuts >/dev/null 2>&1; then
  echo
  echo "warning: 'cachy-shortcuts' is not on your PATH yet."
  echo "It was installed to your user site-packages' bin directory -- you"
  echo "may need to add ~/.local/bin to PATH, or start a new shell."
  exit 0
fi

echo
echo "Registering the overlay's own hotkey..."
cachy-shortcuts install-hotkey || {
  echo "Hotkey registration didn't fully succeed -- run 'cachy-shortcuts doctor'"
  echo "to see what was found, then 'cachy-shortcuts install-hotkey --chord ...'"
  echo "to pick one yourself."
  exit 0
}

echo
echo "Done. Run 'cachy-shortcuts doctor' any time to check detection, configs"
echo "and conflicts, or 'cachy-shortcuts overlay' to open it right now."
