"""Hyprland backend -- hyprlang ``bind = `` grammar.

    $mainMod = SUPER
    bind  = $mainMod, Return, exec, kitty
    bind  = $mainMod SHIFT, Q, killactive,
    bindd = SUPER, B, Browser, exec, firefox
    bindm = SUPER, mouse:272, movewindow

Grammar is ``bind[flags] = <MODS>, <KEY>, <DISPATCHER>, <PARAMS>`` where PARAMS
may itself contain commas, so the split is bounded. ``bindd`` carries a
human description in third position, which is where our ``description`` comes
from and goes back to.

Two things separate this from the otherwise-similar Mango format:

* **Variables.** ``$mainMod`` (and ``$terminal``, ``$menu``, ...) are defined
  in the config and used inside binds. Nothing parses without expanding them
  first, and nothing round-trips unless the *unexpanded* text is kept too.
* **Submaps.** Binds between ``submap = name`` and ``submap = reset`` only fire
  inside that mode, so they are tagged and scoped out of conflict detection
  rather than compared against global chords.

``unbind = MODS, KEY`` removes every bind made on that chord before it, in any
submap, and ``unbind = all`` removes them all. ``source =`` is evaluated
inline, so an unbind in a file sourced later reaches binds from earlier files;
``read`` walks the files in that order. Unbound binds are kept but marked
disabled, so the overlay still lists them and conflict checks skip them.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .. import APP_IDS, RULE_MARKER
from ..model import Chord, Shortcut, SourceRef, infer_category
from . import _lua
from .base import Backend, FloatRule, escape_regex

# Flag letters Hyprland accepts after ``bind``. Spelled out rather than [a-z]*
# so an unrelated key that merely starts with "bind" isn't parsed as one.
# ``k`` (per-device) is left out on purpose: it puts a DEVICE field after the
# key, which _split_fields would read as the dispatcher.
_BIND_RE = re.compile(
    r"^(?P<kw>bind(?P<flags>[lrenmtidopscagux]*))(?P<eq>\s*=\s*)(?P<rest>.*)$"
)
_SOURCE_RE = re.compile(r"^source\s*=\s*(?P<path>.+?)\s*$")
_VAR_RE = re.compile(r"^\$(?P<name>\w+)\s*=\s*(?P<value>.*?)\s*$")
_SUBMAP_RE = re.compile(r"^submap\s*=\s*(?P<name>.+?)\s*$")
_UNBIND_RE = re.compile(r"^unbind\s*=\s*(?P<rest>.*)$")
_VAR_REF_RE = re.compile(r"\$(\w+)")

# Hyprland's own modifier names, longest first so a greedy scan splits
# "SUPERSHIFT" into SUPER + SHIFT rather than failing on the whole token.
_HYPR_MODS: tuple[str, ...] = (
    "SUPER",
    "SHIFT",
    "CONTROL",
    "CTRL",
    "ALT",
    "LOGO",
    "CAPS",
    "MOD1",
    "MOD2",
    "MOD3",
    "MOD4",
    "MOD5",
    "WIN",
)

_HYPR_MOD_SPELLING = {
    "super": "SUPER",
    "ctrl": "CTRL",
    "alt": "ALT",
    "shift": "SHIFT",
}

# Canonical key -> the spelling Hyprland's own default config uses. Only needed
# when emitting a brand-new bind; parsed binds keep their original spelling.
_HYPR_KEY_SPELLING = {
    "return": "Return",
    "kp_enter": "KP_Enter",
    "escape": "Escape",
    "space": "space",
    "tab": "Tab",
    "backspace": "BackSpace",
    "delete": "Delete",
    "insert": "Insert",
    "slash": "slash",
    "backslash": "backslash",
    "bracketleft": "bracketleft",
    "bracketright": "bracketright",
    "minus": "minus",
    "equal": "equal",
    "comma": "comma",
    "period": "period",
    "semicolon": "semicolon",
    "apostrophe": "apostrophe",
    "grave": "grave",
    "home": "Home",
    "end": "End",
    "page_up": "Prior",
    "page_down": "Next",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "print": "Print",
}

_XF86_SPELLING = {
    "xf86audioraisevolume": "XF86AudioRaiseVolume",
    "xf86audiolowervolume": "XF86AudioLowerVolume",
    "xf86audiomute": "XF86AudioMute",
    "xf86audiomicmute": "XF86AudioMicMute",
    "xf86audioplay": "XF86AudioPlay",
    "xf86audiopause": "XF86AudioPause",
    "xf86audionext": "XF86AudioNext",
    "xf86audioprev": "XF86AudioPrev",
    "xf86monbrightnessup": "XF86MonBrightnessUp",
    "xf86monbrightnessdown": "XF86MonBrightnessDown",
}

# Window-rule grammar has changed twice, and neither change was backwards
# compatible, so which one to write has to be decided from the installed
# Hyprland rather than picked once:
#
#   < 0.45   windowrulev2 = float, class:^(x)$
#   0.45+    windowrule   = float, class:^(x)$      (v2 renamed to windowrule)
#   0.53+    windowrule   = float on, match:class ^(x)$
#
# On 0.53 the older form is not merely deprecated -- it fails to parse with
# "invalid field float: missing a value".
_WINDOWRULE_RENAME = (0, 45)
_WINDOWRULE_REGRAMMAR = (0, 53)


def split_mods(field: str) -> list[str]:
    """Split Hyprland's modifier field into individual modifier names.

    Hyprland matches its modmask by substring, so ``SUPER SHIFT``,
    ``SUPER+SHIFT``, ``SUPER_SHIFT`` and plain ``SUPERSHIFT`` are all the same
    chord. Anything that isn't a known modifier is returned as-is, so the
    caller's ``normalize_mod`` still raises on a genuine typo instead of it
    being silently dropped.
    """
    out: list[str] = []
    for token in re.split(r"[+\s_]+", field.strip()):
        if not token:
            continue
        out.extend(_decompose(token))
    return out


def _decompose(token: str) -> list[str]:
    """Greedily split a run-together modifier token into known names."""
    upper = token.upper()
    parts: list[str] = []
    i = 0
    while i < len(upper):
        for mod in _HYPR_MODS:
            if upper.startswith(mod, i):
                parts.append(mod)
                i += len(mod)
                break
        else:
            # Not a modifier run at all -- hand the whole token back untouched
            # so the error surfaces with the text the user actually wrote.
            return [token]
    return parts


class HyprlandBackend(Backend):
    name = "hyprland"
    display_name = "Hyprland"

    @property
    def line_comment(self) -> str:  # type: ignore[override]
        return "--" if self.lua_config() is not None else "#"

    def __init__(self, config_root: Path | None = None) -> None:
        # HYPRLAND_CONFIG only applies to the default location: a caller that
        # names a root (the tests, a second machine's tree) means that root.
        self._honour_env = config_root is None
        self._root = config_root or (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "hypr"
        )

    # --- discovery ---------------------------------------------------------

    def main_config(self) -> Path:
        """The file Hyprland loads, found the way Hyprland finds it.

        An explicit HYPRLAND_CONFIG, then hyprland.lua, then hyprland.conf
        (Jeremy.cpp getCfgPath, v0.56.2). Since 0.55 a hyprland.lua wins
        over a hyprland.conf beside it, and the .conf is then ignored.
        """
        if self._honour_env:
            explicit = os.environ.get("HYPRLAND_CONFIG")
            if explicit:
                return Path(os.path.expanduser(explicit))
        lua = self._root / "hyprland.lua"
        if lua.exists():
            return lua
        main = self._root / "hyprland.conf"
        if not main.exists():
            fallback = Path("/etc/hypr/hyprland.conf")
            main = fallback if fallback.exists() else main
        return main

    def lua_config(self) -> Path | None:
        """The Lua config Hyprland is running, if that is what it loads."""
        main = self.main_config()
        return main if main.suffix == ".lua" else None

    def config_paths(self) -> list[Path]:
        # A hyprland.conf beside a hyprland.lua is ignored by Hyprland, so it
        # is never listed: reading it would show binds that do nothing, and
        # writing to it would report a success that isn't one.
        if self.lua_config() is not None:
            return self._lua_paths()
        out: list[Path] = []
        self._collect(self.main_config(), out, set())
        return out

    def is_installed(self) -> bool:
        # The binary ships as ``Hyprland``; some builds add a lowercase alias.
        if shutil.which("Hyprland") or shutil.which("hyprland"):
            return True
        return self.main_config().exists()

    def _collect(self, path: Path, out: list[Path], visited: set[Path]) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved in visited or not path.exists():
            return
        visited.add(resolved)
        out.append(path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        for target in self._sourced_paths(text, path.parent):
            self._collect(target, out, visited)

    @staticmethod
    def _sourced_paths(text: str, base: Path) -> list[Path]:
        """Resolve every ``source =`` in one file, expanding globs.

        Hyprland accepts a glob (``source = ~/.config/hypr/conf/*.conf``); the
        matches are sorted so the include order -- and therefore which
        definition of a variable wins -- is deterministic.
        """
        found: list[Path] = []
        for line in text.splitlines():
            stripped = _code(line).strip()
            if stripped.startswith("#"):
                continue
            m = _SOURCE_RE.match(stripped)
            if not m:
                continue
            target = os.path.expanduser(m.group("path").strip())
            candidate = Path(target)
            if not candidate.is_absolute():
                candidate = base / candidate
            if any(ch in str(candidate) for ch in "*?["):
                root = Path(candidate.anchor or ".")
                pattern = str(candidate)
                if candidate.is_absolute():
                    pattern = str(candidate.relative_to(root))
                try:
                    found.extend(sorted(root.glob(pattern)))
                except (OSError, ValueError):
                    continue
            else:
                found.append(candidate)
        return found

    # --- variables ---------------------------------------------------------

    def variables(self) -> dict[str, str]:
        """Every ``$name = value`` across the config set, fully expanded.

        Read from disk rather than from the text handed to ``parse`` because a
        bind in an included file routinely uses a variable defined in the main
        one.
        """
        raw: dict[str, str] = {}
        for path in self.config_paths():
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            raw.update(_collect_variables(text))
        return {name: _expand(value, raw) for name, value in raw.items()}

    # --- parsing -----------------------------------------------------------

    def parse(self, text: str, path: Path, submap: str = "") -> list[Shortcut]:
        """One file's binds; ``submap`` is the one in effect where it starts.

        A sourced file starts in whatever submap its ``source`` line was in:
        m_currentSubmap is one parser state, and handleSource keeps it.
        """
        if path.suffix == ".lua":
            return self._parse_lua(text, path)
        out: list[Shortcut] = []
        self._walk(text, path, out, self._file_variables(text), submap, None)
        return out

    def _file_variables(self, text: str, variables: dict | None = None) -> dict:
        """The variables in effect in ``text``: the config's, then its own.

        A file handed to us directly -- one not reachable from the main
        config, or text that hasn't been written yet -- still defines its own.
        """
        variables = dict(self.variables() if variables is None else variables)
        local = _collect_variables(text)
        if local:
            merged = {**variables, **local}
            variables.update({n: _expand(v, merged) for n, v in local.items()})
        return variables

    def _walk(
        self,
        text: str,
        path: Path,
        out: list[Shortcut],
        variables: dict,
        submap: str,
        visit,
        trace: list | None = None,
    ) -> str:
        """Append ``text``'s binds to ``out``; returns the submap it ends in.

        An ``unbind`` disables what is already in ``out``. With ``visit``, a
        ``source`` line hands each file and the current submap to it, and
        carries on in the submap that file leaves. ``trace`` collects every
        line, and each file's end, in load order (see ``placement``).
        """
        offset = 0
        for lineno, line in enumerate(text.splitlines(keepends=True), start=1):
            stripped = _code(line).strip()
            if not stripped or stripped.startswith("#"):
                offset += len(line)
                continue
            if trace is not None:
                trace.append(_Line(path, offset, offset + len(line), stripped, submap))

            sub = _SUBMAP_RE.match(stripped)
            if sub:
                submap = _submap_name(sub.group("name"))
                offset += len(line)
                continue

            unbind = _UNBIND_RE.match(stripped)
            if unbind:
                _apply_unbind(
                    out,
                    unbind.group("rest"),
                    variables,
                    f"`{stripped}` at {path}:{lineno}",
                )
                offset += len(line)
                continue

            if visit is not None and _SOURCE_RE.match(stripped):
                for target in self._sourced_paths(stripped, path.parent):
                    submap = visit(target, submap)
                offset += len(line)
                continue

            shortcut = self._bind(line, stripped, lineno, offset, path, variables, submap)
            if shortcut is not None:
                out.append(shortcut)
            offset += len(line)
        if trace is not None:
            trace.append(_Line(path, len(text), len(text), "", submap))
        return submap

    def _bind(
        self,
        line: str,
        stripped: str,
        lineno: int,
        offset: int,
        path: Path,
        variables: dict,
        submap: str,
    ) -> Shortcut | None:
        m = _BIND_RE.match(stripped)
        if not m:
            return None
        fields = _split_fields(m.group("rest"), described="d" in m.group("flags"))
        if fields is None:
            return None
        (
            mods_raw,
            key_raw,
            description_raw,
            dispatcher_raw,
            params_raw,
            seps,
            had_params,
        ) = fields

        try:
            chord = Chord.from_parts(
                split_mods(_expand(mods_raw, variables)),
                _expand(key_raw, variables),
            )
        except (KeyError, ValueError):
            return None

        action = _join_action(
            _expand(dispatcher_raw, variables), _expand(params_raw, variables)
        )
        description = _expand(description_raw, variables).strip()
        indent = len(line) - len(line.lstrip())
        flags = m.group("flags") or ""
        return Shortcut(
            chord=chord,
            action=action,
            description=description,
            category=infer_category(action, description),
            source=SourceRef(
                backend=self.name,
                path=path,
                start=offset + indent,
                end=offset + indent + len(stripped),
                line=lineno,
            ),
            raw=stripped,
            extras={
                "flags": flags,
                "eq": m.group("eq"),
                "seps": seps,
                "mods_raw": mods_raw,
                "key_raw": key_raw,
                "description_raw": description_raw,
                "dispatcher_raw": dispatcher_raw,
                "params_raw": params_raw,
                "had_params": had_params,
                # `u` (submap-universal) fires in every submap, the global
                # one included (KeybindManager.cpp:652), so it competes with
                # global chords wherever it is written.
                "submap": "" if "u" in flags else submap,
                "block": submap,
            },
        )

    def read(self) -> list[Shortcut]:
        """Every bind, in the order Hyprland evaluates them.

        ``source =`` is inline, so a sourced file's binds sit where its
        ``source`` line is, in the submap in effect there, and an ``unbind``
        reaches every bind before it -- including one from the main file or
        an earlier include, which is the usual "overrides file" layout.
        """
        if self.lua_config() is not None:
            return self._read_lua()
        return self._load()

    def _load(self, trace: list | None = None) -> list[Shortcut]:
        paths = self.config_paths()
        if not paths:
            return []
        out: list[Shortcut] = []
        visited: set[Path] = set()
        variables = self.variables()

        def visit(path: Path, submap: str) -> str:
            try:
                resolved = path.resolve()
            except OSError:
                return submap
            if resolved in visited or not path.exists():
                return submap
            visited.add(resolved)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return submap
            file_vars = self._file_variables(text, variables)
            return self._walk(text, path, out, file_vars, submap, visit, trace)

        visit(paths[0], "")
        return out

    def placement(
        self, path: Path, offset: int, rendered: str
    ) -> tuple[Path, int, str, str] | None:
        """After the last ``unbind`` that would remove a bind put at path:offset.

        An ``unbind`` removes the binds loaded before it, and an overrides
        file sourced at the end of the main config is the usual place for
        one -- so the chords claimant() reports free are exactly the ones a
        new bind in the main file loses again. Placed straight after the
        last such unbind, the new bind is the override it was meant to be.
        """
        if self.lua_config() is not None:
            return self._lua_placement(path, offset, rendered)
        trace: list[_Line] = []
        self._load(trace)
        variables = self.variables()
        probe = self._bind(rendered, rendered.strip(), 1, 0, path, variables, "")
        if probe is None:
            return None
        try:
            where = path.resolve()
        except OSError:
            return None
        here = next(
            (
                i
                for i, line in enumerate(trace)
                if line.start >= offset and line.path.resolve() == where
            ),
            None,
        )
        if here is None:
            return None
        last = None
        for line in trace[here:]:
            unbind = _UNBIND_RE.match(line.text)
            if unbind and _unbind_matches(unbind.group("rest"), probe, variables):
                last = line
        if last is None:
            return None
        text = last.path.read_text(encoding="utf-8")
        raw = text[last.start : last.end]
        indent = raw[: len(raw) - len(raw.lstrip())]
        if not raw.endswith("\n"):
            return (last.path, last.end, "\n" + indent, "")
        return (last.path, last.end, indent, "\n")

    # --- writing -----------------------------------------------------------

    def render(
        self,
        chord: Chord,
        action: str,
        description: str = "",
        extras: dict | None = None,
    ) -> str:
        extras = extras or {}
        if self.lua_config() is not None:
            return self._render_lua(chord, action, description, extras)
        variables = self.variables()

        mods, key = self._spell_chord(chord, extras, variables)
        dispatcher, params = self._spell_action(action, extras, variables)

        flags = extras.get("flags") or ""
        was_described = "d" in flags
        described = bool(description)
        if described and not was_described:
            flags += "d"
        elif not described:
            flags = flags.replace("d", "")

        # Each field alongside the text that stood there before, so the
        # separators can be reused only where they still fit.
        fields = [mods, key]
        before = [extras.get("mods_raw") or "", extras.get("key_raw") or ""]
        if described:
            fields.append(
                _reuse(_safe_description(description), extras.get("description_raw"), variables)
            )
            before.append((extras.get("description_raw") or "") if was_described else "")
        fields.append(dispatcher)
        before.append(extras.get("dispatcher_raw") or "")
        # A dispatcher that takes no parameters is written either way --
        # `killactive,` or `movewindow` -- so follow whichever the line used.
        if params or extras.get("had_params"):
            fields.append(params)
            before.append(extras.get("params_raw") or "")

        return f"bind{flags}{extras.get('eq') or ' = '}{_join_fields(fields, before, extras)}"

    def _spell_chord(
        self, chord: Chord, extras: dict, variables: dict[str, str]
    ) -> tuple[str, str]:
        """Modifier and key text, reusing the config's own spelling if it fits.

        Keeping ``$mainMod`` (and the user's capitalisation) whenever the chord
        is unchanged is what makes an edit to the *command* byte-identical
        everywhere else on the line.
        """
        mods_raw, key_raw = extras.get("mods_raw"), extras.get("key_raw")
        if mods_raw is not None and key_raw is not None:
            try:
                original = Chord.from_parts(
                    split_mods(_expand(mods_raw, variables)), _expand(key_raw, variables)
                )
            except (KeyError, ValueError):
                original = None
            if original == chord:
                return mods_raw, key_raw

        spelled = [_HYPR_MOD_SPELLING.get(m, m.upper()) for m in chord.mods]
        # Write new binds the way the surrounding config does: if a variable
        # stands for the super key, use it rather than spelling SUPER out.
        super_var = _super_variable(variables)
        if super_var and spelled and spelled[0] == "SUPER":
            spelled[0] = super_var
        return " ".join(spelled), _hypr_key_spelling(chord.key)

    def _spell_action(
        self, action: str, extras: dict, variables: dict[str, str]
    ) -> tuple[str, str]:
        """Dispatcher and params, reusing the unexpanded text when it still fits."""
        dispatcher_raw = extras.get("dispatcher_raw")
        params_raw = extras.get("params_raw")
        if dispatcher_raw is not None and params_raw is not None:
            expanded = _join_action(
                _expand(dispatcher_raw, variables), _expand(params_raw, variables)
            )
            if expanded == action.strip():
                return dispatcher_raw, params_raw

        text = action.strip()
        if " " in text:
            dispatcher, params = text.split(" ", 1)
        else:
            dispatcher, params = text, ""
        return dispatcher, params

    def insertion_point(self, text: str) -> tuple[int, str, str]:
        """After the last *global* bind -- never inside a submap.

        A bind appended between ``submap = resize`` and ``submap = reset`` only
        fires inside that mode, which is a silent way for a new binding to
        appear to do nothing.
        """
        if self.lua_config() is not None:
            return self._lua_insertion_point(text)
        last_end = 0
        first_submap: int | None = None
        offset = 0
        submap = ""
        for line in text.splitlines(keepends=True):
            stripped = _code(line).strip()
            sub = _SUBMAP_RE.match(stripped)
            if sub:
                submap = _submap_name(sub.group("name"))
                if submap and first_submap is None:
                    first_submap = offset
            elif not submap and _BIND_RE.match(stripped):
                last_end = offset + len(line.rstrip("\n"))
            offset += len(line)
        if last_end:
            return (last_end, "\n", "")
        if first_submap is not None:
            # No global binds to sit beside, and appending at the end of a
            # submap-only config would scope the new one to that mode.
            return (first_submap, "", "\n\n")
        prefix = "" if text.endswith("\n") or not text else "\n"
        return (len(text), prefix, "\n")

    # --- Lua config ---------------------------------------------------------
    #
    # Hyprland 0.55+ loads hyprland.lua; see _lua.py for how it is read. A bind
    # the scanner can place exactly (top-level, keys that fold to a string) is
    # editable and carries its character span. A bind a loop or helper function
    # generates is found by evaluating the config instead, and is listed with no
    # span, which is what keeps the editor from rewriting the loop around it.

    def _lua_tree(self) -> list[_LuaFile]:
        """The config's Lua files in ``require`` order, each read once."""
        entry = self.main_config()
        root = entry.parent
        files: list[_LuaFile] = []
        seen: set[Path] = set()

        def visit(path: Path) -> None:
            key = _resolved(path)
            if key in seen or not path.is_file():
                return
            seen.add(key)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return
            toks = _lua.tokenize(text)
            files.append(_LuaFile(path, text, toks))
            for module in _lua.required_modules(toks):
                target = _lua.resolve_module(root, module)
                if target is not None:
                    visit(target)

        visit(entry)
        return files

    def notes(self) -> list[str]:
        if self.lua_config() is None:
            return []
        if _lua.find_lua() is None:
            return [
                "`lua` was not found, so binds made by loops or helper functions "
                "are not listed (install lua to see them)"
            ]
        entry = self.main_config()
        result = _lua.evaluate(entry, entry.parent)
        if result is None:
            return [
                "evaluating the Lua config failed or timed out; only binds that can "
                "be placed by reading the files are listed"
            ]
        out = [f"error while evaluating: {e}" for e in result.errors]
        if result.missing:
            out.append("require() could not find: " + ", ".join(result.missing))
        generated = sum(1 for s in self.read() if s.extras.get("generated"))
        out.append(
            f"evaluated with lua; {generated} bind(s) made by loops or helper "
            "functions are listed read-only"
        )
        return out

    def _lua_paths(self) -> list[Path]:
        """Every Lua file of the config, the one new binds belong in first.

        That is the file with the most top-level binds -- ``config/binds.lua``
        in the CachyOS profile -- rather than ``hyprland.lua``, which only
        requires the others.
        """
        files = self._lua_tree()
        if not files:
            return []
        consts = _lua.constants([f.toks for f in files])
        best, best_count = files[0].path, 0
        for f in files:
            count = sum(
                1
                for s in _lua.find_sites(f.text, f.toks, consts)
                if s.kind == "bind" and s.top and not s.submap and s.keys is not None
            )
            if count > best_count:
                best, best_count = f.path, count
        return [best] + [f.path for f in files if f.path != best]

    def _parse_lua(self, text: str, path: Path) -> list[Shortcut]:
        """One Lua file's editable binds, from the text alone (no evaluation).

        This is what validates a write, so it must be exactly what the file
        says: binds a loop generates are not in it. Constants defined in the
        config's other files still fold, as they do for ``read``.
        """
        toks = _lua.tokenize(text)
        here = _resolved(path)
        others = (
            [f.toks for f in self._lua_tree() if _resolved(f.path) != here]
            if self.lua_config() is not None
            else []
        )
        consts = _lua.constants(others + [toks])
        out: list[Shortcut] = []
        for site, shortcut in self._lua_static(text, toks, path, consts):
            if site.kind == "unbind" and site.keys is not None and not site.generated:
                _apply_lua_unbind(
                    out, site.keys, f"`hl.unbind({site.keys!r})` at {path}:{site.line}"
                )
            elif shortcut is not None:
                out.append(shortcut)
        return out

    def _lua_static(
        self, text: str, toks: list, path: Path, consts: dict[str, str]
    ) -> list[tuple[_lua.Site, Shortcut | None]]:
        """Every call in a file; a Shortcut for each bind that can be edited."""
        super_var = next(
            (n for n in _lua.top_level_names(toks) if consts.get(n) == "SUPER"), None
        )
        out: list[tuple[_lua.Site, Shortcut | None]] = []
        for site in _lua.find_sites(text, toks, consts):
            shortcut = None
            if (
                site.kind == "bind"
                and not site.generated
                and site.keys is not None
                and len(site.args) >= 2
            ):
                shortcut = self._lua_shortcut(site, text, toks, path, consts, super_var)
            out.append((site, shortcut))
        return out

    def _lua_shortcut(
        self,
        site: _lua.Site,
        text: str,
        toks: list,
        path: Path,
        consts: dict[str, str],
        super_var: str | None,
    ) -> Shortcut | None:
        try:
            chord = lua_chord(site.keys)
        except (KeyError, ValueError):
            return None
        piece = lambda rng: text[slice(*_lua.span(toks, rng))]  # noqa: E731
        arg_spans = [_lua.span(toks, rng) for rng in site.args]
        seps = [
            text[arg_spans[i][1] : arg_spans[i + 1][0]] for i in range(len(arg_spans) - 1)
        ]
        opts_raw = piece(site.args[2]) if len(site.args) > 2 else None
        action = _lua.action_text(text, toks, site.args[1], consts)
        description = _lua.opts_description(opts_raw, consts)
        flags = _lua.flag_names(opts_raw)
        return Shortcut(
            chord=chord,
            action=action,
            description=description,
            category=infer_category(action, description),
            source=SourceRef(
                backend=self.name,
                path=path,
                start=site.start,
                end=site.end,
                line=site.line,
            ),
            raw=text[site.start : site.end],
            extras={
                "lua": True,
                "keys": site.keys,
                "keys_raw": piece(site.args[0]),
                "dsp_raw": piece(site.args[1]),
                "opts_raw": opts_raw,
                "action0": action,
                "description0": description,
                "head": text[site.start : arg_spans[0][0]],
                "seps": seps,
                "tail": text[arg_spans[-1][1] : site.end],
                "flags": flags,
                "super_var": super_var,
                # submap_universal fires in every submap, the global one
                # included, so it competes with global chords wherever it is.
                "submap": "" if "submap_universal" in flags else site.submap,
                "block": site.submap,
            },
        )

    def _read_lua(self) -> list[Shortcut]:
        """Every bind, in the order Hyprland evaluates them.

        With ``lua`` available the config is evaluated: that fixes the order,
        applies each ``hl.unbind``, and finds the binds loops and helpers make.
        Without it only what the scanner can place is listed.
        """
        files = self._lua_tree()
        if not files:
            return []
        consts = _lua.constants([f.toks for f in files])
        per_file = {
            _resolved(f.path): (
                f,
                self._lua_static(f.text, f.toks, f.path, consts),
            )
            for f in files
        }
        entry = self.main_config()
        result = _lua.evaluate(entry, entry.parent)
        if result is None:
            return self._lua_static_walk(per_file, _resolved(files[0].path), entry.parent)
        return self._lua_eval_walk(per_file, result)

    def _lua_order(self, per_file: dict, start: Path, root: Path):
        """Every call in the order Hyprland runs it: ``(file, site, shortcut)``.

        A ``require``d file runs where its ``require`` is. Each file ends with
        ``(file, None, None)`` so a position past its last call has a place.
        """
        done: set[Path] = set()

        def walk(key: Path):
            if key in done:
                return
            done.add(key)
            file, items = per_file[key]
            for site, shortcut in items:
                if site.kind == "require":
                    target = (
                        None if site.generated else _lua.resolve_module(root, site.module)
                    )
                    if target is not None and _resolved(target) in per_file:
                        yield from walk(_resolved(target))
                else:
                    yield file, site, shortcut
            yield file, None, None

        yield from walk(start)

    def _lua_static_walk(self, per_file: dict, start: Path, root: Path) -> list[Shortcut]:
        """The scanner's binds, a ``require``d file where its ``require`` is."""
        out: list[Shortcut] = []
        for file, site, shortcut in self._lua_order(per_file, start, root):
            if site is None:
                continue
            if site.kind == "unbind":
                if site.keys is not None and not site.generated:
                    _apply_lua_unbind(
                        out,
                        site.keys,
                        f"`hl.unbind({site.keys!r})` at {file.path}:{site.line}",
                    )
            elif shortcut is not None:
                out.append(shortcut)
        return out

    def _lua_eval_walk(self, per_file: dict, result: _lua.EvalResult) -> list[Shortcut]:
        out: list[Shortcut] = []
        used: set[int] = set()
        for rec in result.records:
            home = per_file.get(_resolved(Path(rec.get("file", ""))))
            line = int(rec.get("line") or 0)
            site = shortcut = None
            if home is not None:
                for candidate, found in home[1]:
                    if candidate.kind == rec["op"] and candidate.line <= line <= candidate.end_line:
                        site, shortcut = candidate, found
                        break
            if rec["op"] == "unbind":
                _apply_lua_unbind(
                    out,
                    rec["keys"],
                    f"`hl.unbind({rec['keys']!r})` at {rec.get('file')}:{line}",
                )
                continue
            if (
                shortcut is not None
                and id(site) not in used
                and site.keys == rec["keys"]
            ):
                used.add(id(site))
                shortcut.extras["dsp"] = rec.get("dsp")
                out.append(shortcut)
                continue
            generated = self._lua_generated(rec, home[0] if home else None, site)
            if generated is not None:
                out.append(generated)
        if result.errors:
            # The config stopped partway: what the evaluator never reached is
            # more likely still live than dead, so it is kept.
            for _, items in per_file.values():
                for site, shortcut in items:
                    if shortcut is not None and id(site) not in used:
                        out.append(shortcut)
        return out

    def _lua_generated(
        self, rec: dict, file: _LuaFile | None, site: _lua.Site | None
    ) -> Shortcut | None:
        """A bind a loop or helper made: real, listed, but with nothing to edit."""
        try:
            chord = lua_chord(rec["keys"])
        except (KeyError, ValueError):
            return None
        dsp = rec.get("dsp")
        action = _lua_record_action(dsp)
        opts = rec.get("opts") if isinstance(rec.get("opts"), dict) else {}
        description = str(opts.get("description") or opts.get("desc") or "")
        flags = sorted(k for k, v in opts.items() if v is True)
        where = f"{rec.get('file', '?')}:{rec.get('line', 0)}"
        raw = ""
        if file is not None and site is not None:
            raw = re.sub(r"\s+", " ", file.text[site.start : site.end])
        block = str(rec.get("submap") or "")
        return Shortcut(
            chord=chord,
            action=action,
            description=description,
            category=infer_category(action, description),
            source=None,
            raw=raw,
            extras={
                "lua": True,
                "generated": True,
                "readonly_reason": (
                    "That binding is generated by a loop or helper function in your "
                    f"Hyprland Lua config ({where}) — edit that code directly."
                ),
                "backend": self.name,
                "where": where,
                "keys": rec["keys"],
                "dsp": dsp,
                "flags": flags,
                "submap": "" if "submap_universal" in flags else block,
                "block": block,
            },
        )

    # --- writing Lua ----------------------------------------------------------

    def _render_lua(
        self, chord: Chord, action: str, description: str, extras: dict
    ) -> str:
        """One ``hl.bind(...)`` call, reusing the original text of what didn't change.

        An edit rebuilds the call from its own pieces -- head, the three
        arguments, and the text between them -- so the alignment, ``mainMod ..``
        and ``launchPrefix ..`` the user wrote survive an edit to a different
        argument, byte for byte.
        """
        keys_raw, keys = extras.get("keys_raw"), extras.get("keys")
        same_chord = False
        if keys_raw is not None and keys is not None:
            try:
                same_chord = lua_chord(keys) == chord
            except (KeyError, ValueError):
                pass
        if same_chord:
            keys_expr = keys_raw
        else:
            keys_expr = _lua_keys_expr(
                chord, extras.get("super_var", self._lua_super_var())
            )

        dsp_raw = extras.get("dsp_raw")
        if dsp_raw is not None and action.strip() == extras.get("action0"):
            dsp_expr = dsp_raw
        else:
            dsp_expr = _lua_dispatcher(action)

        opts_raw = extras.get("opts_raw")
        if description == extras.get("description0", ""):
            opts_expr = opts_raw
        else:
            opts_expr = _lua.set_opts_description(opts_raw, description)

        head = extras.get("head", "hl.bind(")
        tail = extras.get("tail", ")")
        seps = extras.get("seps") or [", ", ", "]
        out = head + keys_expr + seps[0] + dsp_expr
        if opts_expr:
            out += (seps[1] if len(seps) > 1 else ", ") + opts_expr
        return out + tail

    def _lua_super_var(self) -> str | None:
        """A name the write-target file defines as ``"SUPER"``, for new binds."""
        paths = self._lua_paths()
        if not paths:
            return None
        try:
            text = paths[0].read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        toks = _lua.tokenize(text)
        consts = _lua.constants([f.toks for f in self._lua_tree()])
        return next((n for n in _lua.top_level_names(toks) if consts.get(n) == "SUPER"), None)

    def _lua_insertion_point(self, text: str) -> tuple[int, str, str]:
        """After the last file-level bind -- never inside a loop, function or submap."""
        toks = _lua.tokenize(text)
        anchor = None
        for site in _lua.find_sites(text, toks, {}):
            if site.kind == "bind" and site.top:
                anchor = site
        if anchor is not None:
            newline = text.find("\n", anchor.end)
            return (len(text) if newline == -1 else newline, "\n", "")
        prefix = "" if text.endswith("\n") or not text else "\n"
        return (len(text), prefix, "\n")

    def _lua_placement(
        self, path: Path, offset: int, rendered: str
    ) -> tuple[Path, int, str, str] | None:
        """After the last ``hl.unbind`` that would remove a bind put at path:offset.

        The same trap as the ``.conf`` grammar: an ``unbind`` removes what ran
        before it, so a new bind above one is silently undone.
        """
        probe = self._parse_lua(rendered, path)
        if not probe or probe[0].extras.get("keys") is None:
            return None
        wanted = _lua_identity(probe[0].extras["keys"])
        files = self._lua_tree()
        if not files or wanted is None:
            return None
        consts = _lua.constants([f.toks for f in files])
        per_file = {
            _resolved(f.path): (f, self._lua_static(f.text, f.toks, f.path, consts))
            for f in files
        }
        entry = self.main_config()
        where = _resolved(path)
        trace = list(self._lua_order(per_file, _resolved(files[0].path), entry.parent))
        here = next(
            (
                i
                for i, (file, site, _) in enumerate(trace)
                if _resolved(file.path) == where
                and (len(file.text) if site is None else site.start) >= offset
            ),
            None,
        )
        if here is None:
            return None
        last = None
        for file, site, _ in trace[here:]:
            if (
                site is not None
                and site.kind == "unbind"
                and not site.generated
                and site.keys is not None
                and _lua_identity(site.keys) == wanted
            ):
                last = (file, site)
        if last is None:
            return None
        file, site = last
        newline = file.text.find("\n", site.end)
        return (file.path, len(file.text) if newline == -1 else newline, "\n", "")

    # --- tiling exception ---------------------------------------------------

    def version(self) -> tuple[int, int] | None:
        """The installed Hyprland's (major, minor), or None if unknowable.

        ``hyprctl`` answers for a running session; the binary answers even
        without one, which is the case when the tool is being set up from a
        different compositor.
        """
        for command in (["hyprctl", "version"], ["Hyprland", "--version"]):
            found = _parse_version(self._run(command))
            if found is not None:
                return found
        return None

    def float_rule(self) -> FloatRule | None:
        """Window rules opening the overlay floating rather than tiled.

        Both app-id spellings fit in one rule because the matcher is a regex.
        The grammar is chosen from the installed Hyprland; when the version
        can't be read, the current one is the safer guess, since that is what
        a machine installing this today is most likely running.
        """
        if self.lua_config() is not None:
            return None  # see unsupported(): nothing here writes Lua
        pattern = f"^({'|'.join(escape_regex(app_id) for app_id in APP_IDS)})$"
        version = self.version()
        if version is None or version >= _WINDOWRULE_REGRAMMAR:
            rules = "\n".join(
                f"windowrule = {rule}, match:class {pattern}"
                for rule in ("float on", "no_blur on")
            )
        else:
            keyword = (
                "windowrule" if version >= _WINDOWRULE_RENAME else "windowrulev2"
            )
            rules = "\n".join(
                f"{keyword} = {prop}, class:{pattern}" for prop in ("float", "noblur")
            )
        body = f"# {RULE_MARKER}: keep the keybinding overlay out of the layout\n{rules}"
        return FloatRule(
            backend=self.name,
            path=self.main_config(),
            body=body,
            marker=f"# {RULE_MARKER}:",
        )

    # --- runtime -----------------------------------------------------------

    def reload(self) -> None:
        self._run(["hyprctl", "reload"])

    def focused_window(self) -> str | None:
        out = self._run(["hyprctl", "-j", "activewindow"])
        if not out:
            return None
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        for key in ("class", "initialClass", "title"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        return None


# --- helpers ---------------------------------------------------------------


def _code(line: str) -> str:
    """``line`` without its comment, as hyprlang reads it.

    Any ``#`` starts a comment, in quotes or not; ``##`` is an escaped,
    literal ``#`` (wiki: hyprlang.md, Comments) and is kept as written.
    """
    i = line.find("#")
    while i != -1:
        if line.startswith("##", i):
            i = line.find("#", i + 2)
            continue
        return line[:i]
    return line


def _apply_unbind(
    earlier: list[Shortcut], rest: str, variables: dict[str, str], where: str = ""
) -> None:
    """Disable what an ``unbind =`` line removes from the binds before it.

    ``all`` clears every bind; otherwise every bind on the chord goes, in any
    submap (KeybindManager::removeKeybind ignores the submap). A line whose
    chord can't be read removes nothing, as a guess would hide a live bind.
    """
    for shortcut in earlier:
        if not shortcut.extras.get("disabled") and _unbind_matches(
            rest, shortcut, variables
        ):
            shortcut.extras["disabled"] = True
            shortcut.extras["disabled_by"] = where


def _unbind_matches(rest: str, shortcut: Shortcut, variables: dict[str, str]) -> bool:
    """Whether ``unbind = <rest>`` removes ``shortcut``.

    Not Chord equality: removeKeybind compares the modmask and the key
    *string* exactly (KeybindManager.cpp:198, v0.56.2), so an unbind of `t`
    leaves a bind on `T` in place.
    """
    value = rest.strip()
    if value == "all":
        return True
    mods_raw, _, key_raw = value.partition(",")
    if not key_raw.strip():
        return False
    wanted = (
        _modmask(_expand(mods_raw, variables)),
        _key_identity(_expand(key_raw.split(",", 1)[0], variables)),
    )
    return (
        _modmask(_expand(shortcut.extras.get("mods_raw", ""), variables)),
        _key_identity(_expand(shortcut.extras.get("key_raw", ""), variables)),
    ) == wanted


@dataclass(frozen=True)
class _Line:
    """One line of the config in load order: file, span, code, submap."""

    path: Path
    start: int
    end: int
    text: str
    submap: str


# stringToModMask (KeybindManager.cpp, v0.56.2): each bit is set when any of
# its names appears anywhere in the field, whatever the case.
_MODMASK_NAMES: tuple[tuple[str, ...], ...] = (
    ("SHIFT",),
    ("CAPS",),
    ("CTRL", "CONTROL"),
    ("ALT", "MOD1"),
    ("MOD2",),
    ("MOD3",),
    ("SUPER", "WIN", "LOGO", "MOD4", "META"),
    ("MOD5",),
)


def _modmask(field: str) -> frozenset[int]:
    upper = field.upper()
    return frozenset(
        bit for bit, names in enumerate(_MODMASK_NAMES) if any(n in upper for n in names)
    )


def _key_identity(key: str) -> tuple:
    """A key as Hyprland's parseKey stores it: a keycode, or the exact string."""
    key = key.strip()
    if key.isdigit() and int(key) > 9:
        return ("code", int(key))
    if key.startswith("code:") and key[5:].isdigit():
        return ("code", int(key[5:]))
    return ("key", key)


def _submap_name(value: str) -> str:
    """The submap a ``submap =`` line enters; "" for the global one.

    The value is a list (handleSubmap: CVarList2), so ``submap = resize,
    reset`` enters ``resize`` and names what resets it.
    """
    name = value.split(",", 1)[0].strip()
    return "" if name == "reset" else name


def _split_fields(rest: str, described: bool):
    """Split a bind's value into fields, keeping the separators verbatim.

    Returns ``(mods, key, description, dispatcher, params, seps, had_params)``
    or None when there aren't enough fields to be a binding. The separators
    come back so ``render`` can rebuild the line with the spacing the user
    wrote, and ``had_params`` records whether a params slot was there at all --
    ``killactive,`` ends in a bare comma while ``movewindow`` has no fourth
    field, and re-emitting either as the other is not a round-trip.
    """
    # mods and key plus a dispatcher; a described bind carries its description
    # between the key and the dispatcher.
    required = 4 if described else 3
    pieces = re.split(r"(\s*,\s*)", rest, maxsplit=required)
    fields = pieces[::2]
    seps = pieces[1::2]
    if len(fields) < required:
        return None
    had_params = len(fields) > required
    params = fields[required] if had_params else ""
    if described:
        mods, key, description, dispatcher = fields[:4]
    else:
        mods, key, dispatcher = fields[:3]
        description = ""
    return mods, key, description, dispatcher, params, seps, had_params


def _join_fields(fields: list[str], before: list[str], extras: dict) -> str:
    """Re-join a bind's fields with the separators the line originally used.

    Reusing them is what keeps an edit byte-identical everywhere it didn't
    touch. Two cases have to fall back to the canonical ``", "``: a changed
    field count (gaining or losing a description shifts every separator), and a
    field that was empty and now isn't -- ``killactive,`` ends in a bare comma,
    and hanging a command off it would read ``exec,firefox``.
    """
    seps = list(extras.get("seps") or [])
    default = _dominant(seps)
    if len(seps) != len(fields) - 1:
        seps = [default] * (len(fields) - 1)
    else:
        seps = [
            default if fields[i + 1] and not before[i + 1] else sep
            for i, sep in enumerate(seps)
        ]
    parts = [fields[0]]
    for sep, field in zip(seps, fields[1:]):
        parts.append(sep)
        parts.append(field)
    return "".join(parts)


def _dominant(seps: list[str]) -> str:
    """The separator spelling this line uses most, or Hyprland's usual one."""
    candidates = [s for s in seps if s.strip(",")]
    if not candidates:
        return ", "
    return max(set(candidates), key=candidates.count)


def _safe_description(description: str) -> str:
    """A description that can't be mistaken for the next field.

    The comma is this grammar's field separator, with no escape for it, so
    "Move left, then right" would be read as a description of "Move left" and a
    dispatcher of "then right" -- a bind that silently does nothing.
    """
    return description.replace(",", ";")


def _reuse(value: str, original: str | None, variables: dict[str, str]) -> str:
    """``original`` when it still expands to ``value``, else ``value`` itself.

    Keeps a ``$variable`` in the file instead of quietly baking in whatever it
    currently expands to.
    """
    if original is not None and _expand(original, variables).strip() == value.strip():
        return original
    return value


def _join_action(dispatcher: str, params: str) -> str:
    return f"{dispatcher.strip()} {params.strip()}".strip()


def _collect_variables(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = _code(line).strip()
        if stripped.startswith("#"):
            continue
        m = _VAR_RE.match(stripped)
        if m:
            out[m.group("name")] = m.group("value")
    return out


def _expand(text: str, variables: dict[str, str], depth: int = 0) -> str:
    """Substitute ``$name`` references, following chains a bounded number of times."""
    if "$" not in text or depth > 8:
        return text

    def swap(match: re.Match) -> str:
        name = match.group(1)
        if name not in variables:
            return match.group(0)
        return _expand(variables[name], variables, depth + 1)

    return _VAR_REF_RE.sub(swap, text)


def _super_variable(variables: dict[str, str]) -> str | None:
    """A ``$name`` the config uses to mean exactly the super key, if it has one."""
    for name, value in variables.items():
        if split_mods(value) == ["SUPER"]:
            return f"${name}"
    return None


def _hypr_key_spelling(key: str) -> str:
    if key in _HYPR_KEY_SPELLING:
        return _HYPR_KEY_SPELLING[key]
    if key in _XF86_SPELLING:
        return _XF86_SPELLING[key]
    if len(key) == 1:
        return key.upper()
    return key


# `hyprctl version` opens with "Hyprland 0.53.0 built from branch..."; the
# binary's own --version leads with the commit and states the release as a tag.
_TAG_RE = re.compile(r"[Tt]ag:\s*v?(\d+)\.(\d+)")
_VERSION_RE = re.compile(r"Hyprland,?\s+v?(\d+)\.(\d+)")


def _parse_version(output: str | None) -> tuple[int, int] | None:
    """Major/minor out of a Hyprland version banner, or None if unreadable."""
    if not output:
        return None
    for pattern in (_VERSION_RE, _TAG_RE):
        m = pattern.search(output)
        if m:
            return (int(m.group(1)), int(m.group(2)))
    return None


# --- Lua helpers -----------------------------------------------------------


@dataclass
class _LuaFile:
    path: Path
    text: str
    toks: list


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def lua_chord(keys: str) -> Chord:
    """A Lua bind's keys string -- ``"SUPER + SHIFT + Left"`` -- as a Chord."""
    parts = [p.strip() for p in keys.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"empty keys: {keys!r}")
    mods: list[str] = []
    for part in parts[:-1]:
        mods.extend(split_mods(part))
    return Chord.from_parts(mods, parts[-1])


def _lua_identity(keys: str) -> tuple | None:
    """What ``hl.unbind`` compares: the modmask and the exact key, as for ``unbind =``."""
    parts = [p.strip() for p in keys.split("+") if p.strip()]
    if not parts:
        return None
    return (_modmask(" ".join(parts[:-1])), _key_identity(parts[-1]))


def _apply_lua_unbind(earlier: list[Shortcut], keys: str, where: str) -> None:
    wanted = _lua_identity(keys)
    if wanted is None:
        return
    for shortcut in earlier:
        if shortcut.extras.get("disabled"):
            continue
        have = shortcut.extras.get("keys")
        if have is not None and _lua_identity(have) == wanted:
            shortcut.extras["disabled"] = True
            shortcut.extras["disabled_by"] = where


def _lua_record_action(dsp) -> str:
    """The action text for a bind the evaluator saw (see ``_lua.action_text``)."""
    if not isinstance(dsp, dict):
        return ""
    if "__dsp" not in dsp:
        return "lua function"
    args = dsp.get("args") or []
    if dsp["__dsp"] == "exec_cmd" and len(args) == 1 and isinstance(args[0], str):
        return f"exec {args[0]}"
    return _lua.format_dsp(dsp)


def _lua_keys_expr(chord: Chord, super_var: str | None) -> str:
    """The keys argument for a new bind, written the way CachyOS's own are.

    ``mainMod .. " + Q"`` when the file defines a name for the super key,
    otherwise a plain ``"SUPER + Q"``.
    """
    parts = [_HYPR_MOD_SPELLING.get(m, m.upper()) for m in chord.mods]
    parts.append(_hypr_key_spelling(chord.key))
    if super_var and parts[0] == "SUPER" and len(parts) > 1:
        return f'{super_var} .. {_lua.lua_string(" + " + " + ".join(parts[1:]))}'
    return _lua.lua_string(" + ".join(parts))


_DSP_CALL_RE = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*\(.*\)$", re.DOTALL)


def _lua_dispatcher(action: str) -> str:
    """An action as the dispatcher argument of ``hl.bind``.

    ``exec firefox`` is the form every backend shares for "run this", so it is
    what the edit form and ``add`` produce; ``window.close()`` is a dispatcher
    as ``read`` shows it, and ``hl.dsp...`` is already source.
    """
    text = action.strip()
    if text.startswith("exec ") and text[5:].strip():
        return f"hl.dsp.exec_cmd({_lua.lua_string(text[5:].strip())})"
    if text.startswith("hl."):
        return text
    if _DSP_CALL_RE.match(text):
        return f"hl.dsp.{text}"
    raise ValueError(
        f"{action!r} isn't an action Hyprland's Lua config can bind; use a command "
        "(exec firefox) or a dispatcher such as window.close()"
    )
