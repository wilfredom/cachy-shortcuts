# cachy-shortcuts

An editable, searchable keybinding atlas for CachyOS — one overlay, one
hotkey, across COSMIC, Niri, Hyprland, and MangoWM.

<img width="995" height="868" alt="cachy-shortcuts" src="https://github.com/user-attachments/assets/dd047562-2226-4f28-b6a5-bb65b534acd8" />

Read-only cheat sheets already exist for this (Omarchy's keybindings overlay,
Niri's built-in `show-hotkey-overlay`, Noctalia's `keybind-cheatsheet`). What
none of them do is let you *change* a binding from inside the overlay, and
none of them work across every compositor. This does both:

- **One tool, four compositors.** COSMIC, Niri, Hyprland, and MangoWM each
  store keybindings in a different format, in a different place, with
  different key-naming conventions. `cachy-shortcuts` reads and writes all of
  them through the same interface, so the same chord (say, `Super+Return` for
  a terminal) is one comparable identity no matter which session you're in.
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

> **Note:** [`docs/overlay-preview.html`](docs/overlay-preview.html) is an
> interactive preview of the *first* overlay design. It has not been updated
> for the current one — the panel styling, the selection highlight and the
> add/edit form all differ. Treat it as a historical sketch, not as
> documentation.

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
entry, registers the overlay's own hotkey, and adds a tiling exception so the
overlay floats over the whole screen instead of being laid out as a window.
It's safe to re-run.

The overlay itself needs GTK4, PyGObject, and
[gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell) — on Arch:

```sh
sudo pacman -S python-gobject gtk4 gtk4-layer-shell
```

The CLI (`list`, `doctor`, `add`, `rm`, ...) works without any of that; only
the visual overlay needs them. `cachy-shortcuts doctor` reports exactly
what's missing.

### Why it isn't tiled

The overlay is a **layer-shell surface** on the overlay layer, anchored to
every edge — no compositor lays those out, so it floats above everything
including fullscreen windows, and reserves no screen space.

That only works if `libgtk4-layer-shell.so` is loaded *before* libwayland-client,
which is why `cachy_shortcuts/ui/_layershell.py` exists and is imported before
anything touches GTK. Get that order wrong and layer-shell silently does
nothing: no error, just an ordinary window that your tiler puts in a column.
`cachy-shortcuts doctor` reports whether it actually loaded.

As a safety net for when gtk4-layer-shell isn't installed, `install-rules`
writes a float rule per compositor, matched on the overlay's app id
(`dev.cachyos.Shortcuts`):

| Compositor | Rule |
|---|---|
| Niri | a `window-rule` with `open-floating true` in `config.kdl` |
| Hyprland | `windowrule = float on, match:class ^(...)$` in `hyprland.conf` |
| MangoWM | `windowrule=isfloating:1,...` in `config.conf` |
| COSMIC | a tiling exception in `com.system76.CosmicSettings.WindowRules/v1/tiling_exception_custom` |

Each is written through the same snapshot → atomic write → validate → rollback
path as every other edit, carries a `cachy-shortcuts` marker comment so
re-running is a no-op, and is undoable with `cachy-shortcuts undo`.

Hyprland's window-rule grammar changed twice and neither change was backwards
compatible, so the version is read (`hyprctl version`, falling back to
`Hyprland --version` when no session is running) and the matching form is
written: the `match:class` grammar on 0.53+, `windowrule = float, class:…` on
0.45–0.52, and `windowrulev2` below that.

## Usage

Press the hotkey (`Super+Slash` by default, unless something else already
owned it — `cachy-shortcuts doctor` shows what was actually registered) to
open the overlay. From there:

| Key | Action |
|---|---|
| Type anything | Search chord, description, or command |
| `↑` / `↓` | Move selection (`PgUp`/`PgDn`, `Home`/`End` too) |
| `Enter` | Edit the selected binding |
| `Ctrl+N` | Add a new binding |
| `Ctrl+D` | Delete the selected binding, `y` to confirm |
| `Esc` | Clear the search, or close the overlay if it's already empty |

Search a word that matches nothing and the list offers `＋ Bind "<word>"…` —
the fastest way in when what you wanted isn't bound yet.

### Adding or editing a binding

`Ctrl+N` opens a form with three fields you `Tab` between. It asks for the
**command first**, because you know what you want to bind before you know
which keys are still free:

| Key | Action |
|---|---|
| Type in **Application or command** | Live list of installed apps; `↑`/`↓` picks |
| `Tab` | Take the highlighted app and move on — this also fills in a free chord |
| In **Shortcut** | Press the combination you want. The field listens the whole time it has focus — including when it already shows a suggested chord, which you simply type over |
| `Backspace` | Clear the chord and listen again |
| `Enter` | Save |
| `Esc` | Cancel (or, while the chord field is listening, just stop listening) |

Pick an app and you're offered an unclaimed chord derived from its name —
`Super+O` for Obsidian, or the next free variant if that's taken. Accept it
and you're done in three keystrokes; press something else and that's the
binding instead.

Inside the Shortcut field, one rule decides what a key press means: an
**unmodified** `Tab`, `Enter` or `Esc` moves on, saves or stops listening,
while anything with a modifier held down is a chord to record. So `Super+Esc`
and `Shift+F5` bind fine; a bare `Tab`, `Enter`, `Esc` or `Backspace` can't be
bound from the form, and neither can `Ctrl+Enter`, which is reserved for taking
a claimed chord. Use `cachy-shortcuts add <chord>` for those.

**A chord that's already bound will not silently become a duplicate.** The
form names whoever owns it and refuses to save; `Ctrl+Enter` takes the chord
for real, unbinding the old one rather than appending a second claim the
compositor would ignore.

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
cachy-shortcuts install-rules           # add the tiling exception (--dry-run to preview)
```

Add `--backend {niri,hyprland,cosmic,mango}` to target a specific compositor, or
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
- **Niri live-reloads**, so an edit there applies with no extra step. Hyprland
  reloads via `hyprctl reload`, Mango via `mmsg -d reload_config`. COSMIC's
  settings daemon watches its own config file.
- **Hyprland's `$variables` survive an edit.** `$mainMod` and friends are
  expanded for display and comparison but written back unexpanded, and a bind
  with a description stays a `bindd`. Binds inside a `submap` are shown and
  tagged, and their chords are compared only against that submap — a resize
  mode reusing `Super+H` is not a conflict.
- **Your shell is detected, not assumed.** Hyprland is run bare as often as
  it's run under Noctalia, so `install-hotkey` and `doctor` report which shell
  (if any) they found, and the overlay takes its colours from that one.

Each backend's module docstring in `cachy_shortcuts/backends/` documents the
exact grammar it parses, if you're curious or debugging a parse.

## Development

```sh
pip install -e ".[dev]"
pytest
```

The core (models, parsers, the write path, conflict detection, the CLI, the
tiling-exception rules, the palette contrast floor, and both of the overlay's
state machines) is fully covered by tests that run without a Wayland session.

The UI splits along that line deliberately, so the part that *can't* be tested
stays small:

| Module | GTK? | What it holds |
|---|---|---|
| `ui/viewmodel.py` | no | Browsing: filtering, grouping, selection |
| `ui/form_model.py` | no | The add/edit form: focus order, chord capture, app suggestions, conflicts, validation |
| `ui/_layershell.py` | yes | Loads gtk4-layer-shell before GTK, and only that |
| `ui/chord_field.py` | yes | Turns GDK key events into chords |
| `ui/binding_form.py` | yes | Paints a `BindingDraft` |
| `ui/overlay.py` | yes | Window, layer-shell setup, list rendering |

Contrast is not left to taste: `theming.ensure_contrast` measures every
foreground against the panel background and raises anything below its WCAG
floor (7:1 for body text, 4.5:1 for secondary, 3:1 for muted), so adopting a
Noctalia or DMS palette can shift the hues without making the overlay
unreadable.

## License

MIT
