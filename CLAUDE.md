# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
pip install -e ".[dev]"     # editable install + pytest
pytest                      # whole suite (testpaths = tests, set in pyproject.toml)
pytest tests/test_editor.py                                  # one file
pytest tests/test_form_model.py::TestChordCapture            # one class
pytest tests/test_form_model.py -k suggested_chord           # one test
pytest -q                                                    # what CI-less review runs
```

No linter or formatter is configured — match the surrounding style rather than
reaching for a tool. There is no CI workflow; `pytest` passing is the bar.

The CLI runs on a bare Python 3.11+ with **no runtime dependencies** (the
parsers are hand-rolled on purpose). Only the overlay needs PyGObject, GTK4 and
gtk4-layer-shell, which are system packages, not wheels. `pyyaml` is optional:
a dependency-free fallback parser covers the bundled cheat-sheet schema.

```sh
cachy-shortcuts doctor      # detection, config paths, conflicts, missing deps
cachy-shortcuts list        # bindings, grouped
cachy-shortcuts overlay     # the GUI (needs a Wayland session)
cachy-shortcuts undo        # roll back the most recent write
```

## Environment limits when working here

This repo is usually edited in a container with **no Wayland session and no
GTK4/PyGObject**, so `cachy_shortcuts/ui/overlay.py`, `binding_form.py`,
`chord_field.py` and `_layershell.py` cannot be imported or executed — only
`py_compile`d. That constraint is the reason for the architecture below, and
changes to those four files have to be verified by hand on a real machine
(`cachy-shortcuts doctor`, then `cachy-shortcuts overlay`). Say so explicitly
when reporting such a change rather than implying it was run.

## Architecture

### The canonical `Chord` is what makes everything else work

`normalize.py` collapses every dialect's spelling into one canonical form:
niri's `Mod+Shift+Slash`, COSMIC's `(modifiers: [Super, Shift], key: "slash")`
and mango's `SUPER+SHIFT,slash` are all one `Chord`. If a spelling fails to
collapse, conflict detection silently passes and search silently misses — so
the translation tables are deliberately explicit rather than clever. New key or
modifier spellings go in `normalize.py`, never in a backend.

`model.py` holds `Chord`, `Shortcut` (with `SourceRef`: file + exact character
span, and `extras` for backend-specific properties) and category inference.
Everything above the backend layer speaks only these types.

### Backends own format knowledge, and nothing else does

`backends/base.py` defines the contract; `niri.py`, `hyprland.py`, `cosmic.py`,
`mango.py` implement it. Each module's docstring documents the grammar it
parses. The abstract surface is small: `config_paths()` (includes resolved,
write-target first), `parse(text, path)` (pure, no I/O — this is why parsing is
fully testable), `render(chord, action, description, extras)`,
`insertion_point(text) -> (offset, prefix, suffix)`, plus optional
`float_rule()`, `reload()` and `focused_window()`.

Offsets are **character** offsets into the decoded UTF-8 text, not bytes.

Round-tripping matters: `extras` carries what a backend must not lose across an
edit (niri's `allow-when-locked`, mango's `bind` flags, Hyprland's `bindd` form
and unexpanded `$mainMod` variables), and `render` reuses the config's original
spelling when the chord is unchanged, so editing one field doesn't reformat the
others. Adding a backend means adding it to `ALL_BACKENDS` in
`backends/__init__.py`, teaching `detect.py` its process/session markers, and
adding a fixture tree under `tests/fixtures/`.

### Every write is snapshot → atomic write → re-parse → rollback

`editor.py` is the only module that mutates config files, and every operation
(`add`, `update`, `delete`, `take_over`, `undo_last`) follows that shape via
`_commit`. Edits are surgical: the recorded span is replaced and nothing else
moves, so comments, ordering and formatting survive. Validation re-parses the
result and rolls the snapshot back if the edit didn't take.
`backup.py` owns snapshots (`~/.local/share/cachy-shortcuts/backups/`),
`write_atomic`, `restore` and pruning. COSMIC is the special case: its
`defaults` file is system-owned, so an edit to a default becomes an override in
the user's `custom` file (`editor._target_file`).

### Conflict detection is scoped, not global

`conflicts.py` compares chords only within the same scope (`_scope`), which is
what lets a Hyprland `submap` reuse `Super+H` without being a conflict, and
skips disabled bindings. `claimant()` and `first_free()` are the two entry
points — `first_free` is reused by both the form's chord suggestion and
`install.py`'s pick of the tool's own hotkey. `Shortcut.owner` recognises
Noctalia / DMS / cachy-shortcuts binds so the tool names who holds a chord
rather than offering to steal it.

### The UI splits along the testable/untestable line

This split is deliberate and load-bearing — keep the GTK files thin.

| Module | GTK? | Holds |
|---|---|---|
| `ui/viewmodel.py` | no | Browsing: filtering, grouping, selection, modes |
| `ui/form_model.py` | no | The add/edit form: focus order, chord arming/capture, app suggestions, conflicts, validation |
| `ui/_layershell.py` | yes | Loads `libgtk4-layer-shell.so` **before** GTK pulls in libwayland-client |
| `ui/chord_field.py` | yes | GDK key events → `Chord` |
| `ui/binding_form.py` | yes | Translates key events into `BindingDraft` calls and paints it |
| `ui/overlay.py` | yes | Window, layer-shell setup, list rendering, save dispatch |

Behaviour changes belong in `viewmodel.py` / `form_model.py`, where
`tests/test_viewmodel.py` and `tests/test_form_model.py` can assert them
headlessly. The GTK files should stay a mechanical translation.

`_layershell.py` must be imported before anything touches GTK: get that order
wrong and layer-shell silently does nothing — no error, just an ordinary window
the tiler puts in a column.

Key handling in the form follows one rule worth knowing before touching it: the
chord field listens for as long as it has focus (including when it already
holds a suggested chord), and an **unmodified** `Tab`/`Enter`/`Esc`/`Backspace`
is navigation while anything with a modifier held is a chord to record.
`Ctrl+Enter` is reserved for taking a claimed chord.

### Supporting modules

- `detect.py` — layered detection: session env vars, then process names, then
  config existence (that last one proves "installed", never "active").
- `appscan.py` — XDG `.desktop` scan feeding the form's type-ahead.
- `theming.py` — reads the running shell's palette (Noctalia, DMS) rather than
  shipping a theme; `ensure_contrast` enforces a WCAG floor (7:1 body, 4.5:1
  secondary, 3:1 muted) so a borrowed palette can't make the overlay
  unreadable. Best-effort by design — every failure path falls back.
- `cheatsheets/` — bundled read-only reference packs for app-owned shortcuts,
  surfaced by focused app id. These have no `SourceRef` and must stay
  uneditable.
- `usage.py` — learning mode counts *lookups*, not presses: the tool never
  executes a shortcut, so it can't count firings. Local disk only.
- `install.py` / `floatrule.py` — the tool's own hotkey and the per-compositor
  tiling exception, both written through the same snapshot/rollback path and
  both idempotent via a marker comment.
