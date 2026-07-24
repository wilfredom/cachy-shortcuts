# cachy-shortcuts

An editable, searchable keybinding atlas for CachyOS — one overlay, one
hotkey, across COSMIC, Niri, and MangoWM.

Read-only cheat sheets already exist for this (Omarchy's keybindings overlay,
Niri's built-in `show-hotkey-overlay`, Noctalia's `keybind-cheatsheet`). What
none of them do is let you *change* a binding from inside the overlay, and
none of them work across all three compositors. This does both:

- **One tool, three compositors.** COSMIC, Niri, and MangoWM each store
  keybindings in a different format, in a different place, with different
  key-naming conventions. `cachy-shortcuts` reads and writes all three
  through the same interface, so the same chord (say, `Super+Return` for a
  terminal) is one comparable identity no matter which session you're in.
- **Edit in place, safely.** Every write is preceded by a snapshot, written
  atomically, and re-parsed to confirm it took effect — a failure at any step
  rolls the file back automatically. Edits are surgical: only the bytes a
  binding occupies are touched, so your comments, ordering, and formatting
  survive.
- **Conflict detection that knows about your shell.** Two bindings claiming
  the same chord is the usual reason a custom keybind "mysteriously stops
  working." The conflict checker also recognizes chords owned by Noctalia and
  DankMaterialShell (their launcher, control-center, and cheatsheet bindings)
  so it won't offer to steal one.
- **App-specific cheat sheets.** Bundled reference packs (Firefox, VS Code,
  Alacritty, Files) surface an app's *own* shortcuts when it was focused
  before you opened the overlay — informational only, since editing an app's
  own keybinds is that app's job, not this tool's.
- **Learning mode, honestly scoped.** This tool never executes your
  shortcuts — it's a reference and editor, not a launcher — so it can't count
  how often you *press* one. It counts how often you *look one up* instead,
  which is arguably the better signal: a binding you keep searching for is
  one you haven't internalized yet.

## Install

### Arch / CachyOS (recommended)

```sh
git clone https://github.com/wilfredom/cachy-shortcuts.git
cd cachy-shortcuts
makepkg -si
cachy-shortcuts install-hotkey
```

### Anything else with Python 3.11+

```sh
git clone https://github.com/wilfredom/cachy-shortcuts.git
cd cachy-shortcuts
./packaging/install.sh
```

This pip-installs the package for your user, installs a `.desktop` launcher
entry, and registers the overlay's own hotkey. It's safe to re-run.

The overlay itself needs GTK4, PyGObject, and
[gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell) — on Arch:

```sh
sudo pacman -S python-gobject gtk4 gtk4-layer-shell
```

The CLI (`list`, `doctor`, `add`, `rm`, ...) works without any of that; only
the visual overlay needs them. `cachy-shortcuts doctor` reports exactly
what's missing.

## Usage

Press the hotkey (`Super+Slash` by default, unless something else already
owned it — `cachy-shortcuts doctor` shows what was actually registered) to
open the overlay. From there:

| Key | Action |
|---|---|
| Type anything | Search chord, description, or command |
| `↑` / `↓` | Move selection |
| `Enter` | Rebind the selected shortcut — press the new chord, `Enter` to save |
| `Ctrl+Enter` | Edit the command instead of the chord (`Tab` cycles installed-app suggestions) |
| `n` | Add a new binding (only while search is empty; `Ctrl+N` always works) |
| `d` | Delete the selected binding, `y` to confirm (only while search is empty; `Ctrl+D` always works) |
| `Esc` | Cancel the current action, or close the overlay |

Conflicts are shown live, in place — composing a chord another binding
already owns turns the row amber and names the owner, before you save
anything.

### CLI

Everything the overlay does is also a command, useful for scripting or a
quick check without leaving the terminal:

```sh
cachy-shortcuts list                    # grouped view of everything bound
cachy-shortcuts list firefox            # filter by chord, label, or command
cachy-shortcuts list --json             # machine-readable
cachy-shortcuts doctor                  # detection, configs, conflicts, deps
cachy-shortcuts conflicts               # just the conflicts
cachy-shortcuts add "Super+N" --app Obsidian
cachy-shortcuts rm "Super+N"
cachy-shortcuts undo                    # revert the most recent change
cachy-shortcuts restore --list          # every snapshot, for anything older
cachy-shortcuts apps obsidian           # search installed .desktop apps
cachy-shortcuts cheatsheet firefox      # preview an app's bundled cheat sheet
cachy-shortcuts cheatsheet --list       # every bundled/user cheat sheet pack
cachy-shortcuts forget --all            # erase lookup history
```

Add `--backend {niri,cosmic,mango}` to target a specific compositor, or
`--all` to see every one with a config on disk regardless of which is
currently running.

### Cheat sheets for apps not bundled

Add a YAML file to `~/.config/cachy-shortcuts/cheatsheets/<name>.yaml`:

```yaml
name: Obsidian
match:
  - obsidian
shortcuts:
  - chord: "Ctrl+O"
    description: "Quick switcher"
  - chord: "Ctrl+Shift+F"
    description: "Search all notes"
```

A file with the same name as a bundled pack (e.g. `firefox.yaml`) replaces it
outright — useful if you've remapped an app's own shortcuts and want the
cheat sheet to match.

## How it works

- **Reads are honest about defaults.** COSMIC's `custom` overrides layer over
  its system `defaults`, with `Disable` entries removed — the overlay shows
  what's actually in effect, not just what you've personally changed.
- **Writes never regenerate a file.** Every parser records the exact
  character span a binding occupies, so an edit replaces only those
  characters. A snapshot is taken first
  (`~/.local/share/cachy-shortcuts/backups/`), the write is atomic, and the
  result is re-parsed to confirm the edit actually took — any failure rolls
  the snapshot back automatically.
- **Niri live-reloads**, so an edit there applies with no extra step. Mango
  reloads via `mmsg -d reload_config`. COSMIC's settings daemon watches its
  own config file.

Each backend's module docstring in `cachy_shortcuts/backends/` documents the
exact grammar it parses, if you're curious or debugging a parse.

## Development

```sh
pip install -e ".[dev]"
pytest
```

The core (models, parsers, the write path, conflict detection, the CLI, and
the overlay's state machine) is fully covered by tests that run without a
Wayland session. The GTK4 rendering layer (`cachy_shortcuts/ui/overlay.py`)
is deliberately thin over that tested core, since it can only be verified by
running it on a real compositor.

## License

MIT
