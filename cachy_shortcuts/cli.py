"""Command line interface.

The CLI exists so every capability is reachable and verifiable without a
compositor or a GUI -- which is also what makes the core testable in CI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import (
    APP_ID,
    __version__,
    appscan,
    backup,
    cheatsheets,
    conflicts,
    detect,
    editor,
    floatrule,
    usage,
)
from .backends.base import Backend
from .model import Category, Chord, Shortcut

# --- output helpers --------------------------------------------------------

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


ACCENT = "38;5;80"
DIM = "38;5;245"
BOLD = "1"
WARN = "38;5;214"


def _resolve_backends(args) -> list[Backend]:
    if getattr(args, "backend", None):
        backend = detect.backend_by_name(args.backend)
        if backend is None:
            _die(f"unknown backend: {args.backend}")
        return [backend]
    if getattr(args, "all", False):
        return detect.detect_installed() or detect.detect_all()
    active = detect.detect_active()
    if active is not None:
        return [active]
    installed = detect.detect_installed()
    if not installed:
        _die(
            "no compositor detected and no config found.\n"
            "Run `cachy-shortcuts doctor` to see what was searched."
        )
    return installed


def _die(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


# --- commands --------------------------------------------------------------


def cmd_list(args) -> int:
    backends = _resolve_backends(args)
    collected: list[tuple[Backend, list[Shortcut]]] = [(b, b.read()) for b in backends]

    if args.json:
        payload = [
            {
                "backend": backend.name,
                "shortcuts": [
                    {
                        "chord": s.chord.canonical,
                        "display": s.chord.display(),
                        "action": s.action,
                        "description": s.label,
                        "category": s.category.value,
                        "source": s.source.location if s.source else None,
                        "disabled": bool(s.extras.get("disabled")),
                    }
                    for s in shortcuts
                ],
            }
            for backend, shortcuts in collected
        ]
        print(json.dumps(payload, indent=2))
        return 0

    query = (args.query or "").strip().lower()
    for backend, shortcuts in collected:
        if query:
            shortcuts = [
                s
                for s in shortcuts
                if query in s.chord.display().lower()
                or query in s.label.lower()
                or query in s.action.lower()
            ]
        if not shortcuts:
            continue
        print(_c(f"\n{backend.display_name}", BOLD), _c(f"({len(shortcuts)})", DIM))
        width = max(len(s.chord.display()) for s in shortcuts)
        for category in Category:
            group = [s for s in shortcuts if s.category is category]
            if not group:
                continue
            print(_c(f"\n  {category.value}", DIM))
            for s in sorted(group, key=lambda x: x.chord.display()):
                chord = s.chord.display().ljust(width)
                mark = _c(" (disabled)", WARN) if s.extras.get("disabled") else ""
                print(f"  {_c(chord, ACCENT)}  {_c('→', DIM)} {s.label}{mark}")
    print()
    return 0


def cmd_doctor(args) -> int:
    active = detect.active_backend_name()
    print(_c("cachy-shortcuts doctor", BOLD), _c(f"v{__version__}", DIM))
    print(f"\n  active session : {_c(active or 'not detected', ACCENT if active else WARN)}")
    print(f"  data directory : {backup.data_dir()}")

    total_conflicts = 0
    for backend in detect.detect_all():
        print(_c(f"\n  {backend.display_name}", BOLD))
        paths = backend.config_paths()
        existing = [p for p in paths if p.exists()]
        if not existing:
            print(f"    {_c('no config found', DIM)}")
            for p in paths[:2]:
                print(f"    {_c('searched:', DIM)} {p}")
            continue
        shortcuts = backend.read()
        print(f"    bindings : {len(shortcuts)}")
        for path in existing:
            print(f"    config   : {path}")
        found = conflicts.find_conflicts(shortcuts)
        total_conflicts += len(found)
        if found:
            print(f"    {_c(f'conflicts: {len(found)}', WARN)}")
            for conflict in found:
                print(f"      {_c(conflict.describe(), WARN)}")
        else:
            print(f"    conflicts: {_c('none', ACCENT)}")

        state = floatrule.status_for(backend)
        if state.rule is None:
            print(f"    tiling   : {_c(state.note, DIM)}")
        elif state.installed:
            print(f"    tiling   : {_c('exempt', ACCENT)} ({state.rule.path})")
        else:
            print(
                f"    tiling   : {_c('would be tiled', WARN)}"
                " (fix with: cachy-shortcuts install-rules)"
            )

    print(_c("\n  Overlay dependencies", BOLD))
    for label, module in (("PyGObject", "gi"), ("PyYAML (optional)", "yaml")):
        try:
            __import__(module)
            print(f"    {label}: {_c('ok', ACCENT)}")
        except ImportError:
            note = "required for the overlay" if module == "gi" else "bundled fallback in use"
            print(f"    {label}: {_c('missing', WARN)} ({note})")
    # Import the overlay's own loader rather than probing the typelib directly:
    # loading order is the thing that actually decides whether layer-shell
    # works, and only that module gets it right.
    try:
        from .ui import _layershell

        text = _layershell.status()
        print(f"    gtk4-layer-shell: {_c(text, ACCENT if _layershell.available() else WARN)}")
    except Exception as exc:  # noqa: BLE001 - no GTK at all is a normal state here
        print(f"    gtk4-layer-shell: {_c(f'unavailable ({exc})', WARN)}")
    print(f"    overlay app id  : {_c(APP_ID, DIM)}")

    snapshots = backup.list_snapshots()
    print(_c("\n  Backups", BOLD))
    print(f"    snapshots: {len(snapshots)}")
    if snapshots:
        print(f"    latest   : {snapshots[0].describe()}")

    gaps = usage.top_gaps(limit=3)
    if gaps:
        print(_c("\n  Most looked up", BOLD))
        for gap in gaps:
            print(f"    {gap.describe()}")

    return 1 if total_conflicts else 0


def cmd_conflicts(args) -> int:
    found_any = False
    for backend in _resolve_backends(args):
        found = conflicts.find_conflicts(backend.read())
        if not found:
            continue
        found_any = True
        print(_c(f"\n{backend.display_name}", BOLD))
        for conflict in found:
            print(f"  {_c(conflict.describe(), WARN)}")
    if not found_any:
        print(_c("No conflicts found.", ACCENT))
        return 0
    return 1


def cmd_add(args) -> int:
    backends = _resolve_backends(args)
    if len(backends) > 1:
        _die("multiple compositors found; pick one with --backend")
    backend = backends[0]
    try:
        chord = Chord.parse(args.chord)
    except (KeyError, ValueError) as exc:
        _die(f"bad chord {args.chord!r}: {exc}")

    existing = backend.read()
    claim = conflicts.describe_claimant(chord, existing)
    if claim and not args.force:
        _die(f"{chord.display()} is {claim}. Use --force to take it anyway.")

    command = args.command
    if args.app:
        resolved = appscan.command_for(args.app)
        if resolved is None:
            _die(f"no installed application named {args.app!r}")
        command = resolved
    if not command:
        _die("provide a command, or --app NAME")

    action = editor.wrap_command_as_action(backend, command)
    try:
        result = editor.add(backend, chord, action, args.description or "")
    except editor.EditError as exc:
        _die(str(exc))
    print(f"{_c('added', ACCENT)} {chord.display()} → {command}")
    print(_c(f"  {result.path}", DIM))
    print(_c("  undo with: cachy-shortcuts undo", DIM))
    return 0


def cmd_rm(args) -> int:
    backends = _resolve_backends(args)
    if len(backends) > 1:
        _die("multiple compositors found; pick one with --backend")
    backend = backends[0]
    try:
        chord = Chord.parse(args.chord)
    except (KeyError, ValueError) as exc:
        _die(f"bad chord {args.chord!r}: {exc}")
    target = conflicts.claimant(chord, backend.read())
    if target is None:
        _die(f"{chord.display()} is not bound in {backend.display_name}")
    try:
        result = editor.delete(backend, target)
    except editor.EditError as exc:
        _die(str(exc))
    print(f"{_c('removed', ACCENT)} {chord.display()} ({target.label})")
    print(_c(f"  {result.path}", DIM))
    print(_c("  undo with: cachy-shortcuts undo", DIM))
    return 0


def cmd_undo(args) -> int:
    restored = editor.undo_last()
    if not restored:
        print("Nothing to undo.")
        return 1
    print(_c("restored", ACCENT))
    for path in restored:
        print(f"  {path}")
    return 0


def cmd_restore(args) -> int:
    snapshots = backup.list_snapshots()
    if args.list or not args.snapshot:
        if not snapshots:
            print("No snapshots yet.")
            return 0
        for snapshot in snapshots:
            print(f"  {snapshot.id}  {snapshot.reason}")
        return 0
    match = next((s for s in snapshots if s.id == args.snapshot), None)
    if match is None:
        _die(f"no snapshot {args.snapshot!r}")
    restored = backup.restore(match)
    print(_c(f"restored {len(restored)} file(s) from {match.id}", ACCENT))
    return 0


def cmd_apps(args) -> int:
    apps = appscan.search(args.query or "", limit=args.limit)
    if not apps:
        print("No matching applications.")
        return 1
    width = max(len(a.name) for a in apps)
    for app in apps:
        print(f"  {_c(app.name.ljust(width), ACCENT)}  {_c(app.command, DIM)}")
    return 0


def cmd_forget(args) -> int:
    if args.chord:
        chord = Chord.parse(args.chord)
        removed = usage.forget(chord.canonical)
        print("Forgotten." if removed else "No history for that chord.")
        return 0 if removed else 1
    if not args.all:
        _die("pass --all to erase all lookup history, or give a chord")
    print("Erased lookup history." if usage.forget_all() else "No history to erase.")
    return 0


def cmd_cheatsheet(args) -> int:
    if args.list:
        packs = cheatsheets.available_packs()
        if not packs:
            print("No cheat sheet packs found.")
            return 1
        for pack in packs:
            aliases = ", ".join(pack.match)
            print(f"  {_c(pack.name, ACCENT)}  {_c(f'({aliases})', DIM)}")
        return 0

    if not args.app:
        _die("give an app id (e.g. firefox), or pass --list")
    entries = cheatsheets.load_for(args.app)
    if not entries:
        print(f"No cheat sheet matches {args.app!r}.")
        return 1
    width = max(len(s.chord.display()) for s in entries)
    for s in sorted(entries, key=lambda s: s.chord.display()):
        print(f"  {_c(s.chord.display().ljust(width), ACCENT)}  {_c('→', DIM)} {s.label}")
    return 0


def cmd_overlay(args) -> int:
    try:
        from .ui.overlay import run
    except ImportError as exc:
        _die(
            f"the overlay needs PyGObject and gtk4-layer-shell ({exc}).\n"
            "Run `cachy-shortcuts doctor` to check, then install:\n"
            "  sudo pacman -S python-gobject gtk4 gtk4-layer-shell"
        )
    return run(toggle=args.toggle)


def cmd_install_hotkey(args) -> int:
    from .install import install_hotkey

    return install_hotkey(chord_text=args.chord, dry_run=args.dry_run)


def cmd_install_rules(args) -> int:
    from .install import install_rules

    return 1 if install_rules(dry_run=args.dry_run) else 0


# --- argument parsing ------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cachy-shortcuts",
        description="An editable, searchable keybinding atlas for COSMIC, Niri and MangoWM.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    def add_backend_flags(p):
        p.add_argument("--backend", choices=["niri", "cosmic", "mango"],
                       help="target a specific compositor instead of the active one")
        p.add_argument("--all", action="store_true",
                       help="include every compositor with a config on disk")

    p_list = sub.add_parser("list", help="show bindings, grouped by category")
    p_list.add_argument("query", nargs="?", help="filter by chord, label or command")
    p_list.add_argument("--json", action="store_true", help="machine-readable output")
    add_backend_flags(p_list)
    p_list.set_defaults(func=cmd_list)

    p_doctor = sub.add_parser("doctor", help="report detection, configs, conflicts and deps")
    p_doctor.set_defaults(func=cmd_doctor)

    p_conf = sub.add_parser("conflicts", help="list chords bound more than once")
    add_backend_flags(p_conf)
    p_conf.set_defaults(func=cmd_conflicts)

    p_add = sub.add_parser("add", help="bind a chord to a command")
    p_add.add_argument("chord", help='e.g. "Super+Shift+B"')
    p_add.add_argument("command", nargs="?", help="command to run")
    p_add.add_argument("--app", help="bind an installed application by name")
    p_add.add_argument("--description", help="label shown in the overlay")
    p_add.add_argument("--force", action="store_true", help="take the chord even if claimed")
    add_backend_flags(p_add)
    p_add.set_defaults(func=cmd_add)

    p_rm = sub.add_parser("rm", help="remove a binding")
    p_rm.add_argument("chord")
    add_backend_flags(p_rm)
    p_rm.set_defaults(func=cmd_rm)

    p_undo = sub.add_parser("undo", help="roll back the most recent change")
    p_undo.set_defaults(func=cmd_undo)

    p_restore = sub.add_parser("restore", help="list or restore config snapshots")
    p_restore.add_argument("snapshot", nargs="?", help="snapshot id to restore")
    p_restore.add_argument("--list", action="store_true", help="list snapshots")
    p_restore.set_defaults(func=cmd_restore)

    p_apps = sub.add_parser("apps", help="search installed applications")
    p_apps.add_argument("query", nargs="?")
    p_apps.add_argument("--limit", type=int, default=20)
    p_apps.set_defaults(func=cmd_apps)

    p_forget = sub.add_parser("forget", help="erase lookup history")
    p_forget.add_argument("chord", nargs="?")
    p_forget.add_argument("--all", action="store_true")
    p_forget.set_defaults(func=cmd_forget)

    p_cheat = sub.add_parser("cheatsheet", help="preview an app's bundled cheat sheet")
    p_cheat.add_argument("app", nargs="?", help="app id or name, e.g. firefox")
    p_cheat.add_argument("--list", action="store_true", help="list available packs")
    p_cheat.set_defaults(func=cmd_cheatsheet)

    p_overlay = sub.add_parser("overlay", help="open the overlay (default action)")
    p_overlay.add_argument("--toggle", action="store_true",
                           help="close it instead if already open")
    p_overlay.set_defaults(func=cmd_overlay)

    p_hotkey = sub.add_parser("install-hotkey",
                              help="register this tool's own hotkey in each compositor")
    p_hotkey.add_argument("--chord", help="chord to use (default: first free candidate)")
    p_hotkey.add_argument("--dry-run", action="store_true", help="show what would change")
    p_hotkey.set_defaults(func=cmd_install_hotkey)

    p_rules = sub.add_parser(
        "install-rules",
        help="exempt the overlay from each compositor's tiling layout",
    )
    p_rules.add_argument("--dry-run", action="store_true", help="show what would change")
    p_rules.set_defaults(func=cmd_install_rules)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # Bare invocation opens the overlay -- that's the hotkey's job.
        args = parser.parse_args(["overlay", "--toggle"])
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
