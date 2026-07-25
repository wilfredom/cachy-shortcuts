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
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from .. import APP_IDS, RULE_MARKER
from ..model import Chord, Shortcut, SourceRef, infer_category
from .base import Backend, FloatRule, escape_regex

# Flag letters Hyprland accepts after ``bind``. Spelled out rather than [a-z]*
# so an unrelated key that merely starts with "bind" isn't parsed as one.
_BIND_RE = re.compile(r"^(?P<kw>bind(?P<flags>[lrenmtidopsc]*))(?P<eq>\s*=\s*)(?P<rest>.*)$")
_SOURCE_RE = re.compile(r"^source\s*=\s*(?P<path>.+?)\s*$")
_VAR_RE = re.compile(r"^\$(?P<name>\w+)\s*=\s*(?P<value>.*?)\s*$")
_SUBMAP_RE = re.compile(r"^submap\s*=\s*(?P<name>.+?)\s*$")
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

    def __init__(self, config_root: Path | None = None) -> None:
        self._root = config_root or (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "hypr"
        )

    # --- discovery ---------------------------------------------------------

    def config_paths(self) -> list[Path]:
        main = self._root / "hyprland.conf"
        if not main.exists():
            fallback = Path("/etc/hypr/hyprland.conf")
            main = fallback if fallback.exists() else main
        out: list[Path] = []
        self._collect(main, out, set())
        return out

    def is_installed(self) -> bool:
        # The binary ships as ``Hyprland``; some builds add a lowercase alias.
        if shutil.which("Hyprland") or shutil.which("hyprland"):
            return True
        return any(p.exists() for p in self.config_paths())

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
            stripped = line.strip()
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

    def parse(self, text: str, path: Path) -> list[Shortcut]:
        variables = self.variables()
        # A file handed to us directly -- one not reachable from the main
        # config, or text that hasn't been written yet -- still defines its own.
        local = _collect_variables(text)
        if local:
            merged = {**variables, **local}
            variables.update({n: _expand(v, merged) for n, v in local.items()})

        out: list[Shortcut] = []
        offset = 0
        submap = ""
        for lineno, line in enumerate(text.splitlines(keepends=True), start=1):
            stripped = line.strip()
            content_len = len(line.rstrip("\n"))
            if not stripped or stripped.startswith("#"):
                offset += len(line)
                continue

            sub = _SUBMAP_RE.match(stripped)
            if sub:
                name = sub.group("name")
                submap = "" if name == "reset" else name
                offset += len(line)
                continue

            m = _BIND_RE.match(stripped)
            if not m:
                offset += len(line)
                continue
            fields = _split_fields(m.group("rest"), described="d" in m.group("flags"))
            if fields is None:
                offset += len(line)
                continue
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
                offset += len(line)
                continue

            action = _join_action(
                _expand(dispatcher_raw, variables), _expand(params_raw, variables)
            )
            description = _expand(description_raw, variables).strip()
            indent = len(line) - len(line.lstrip())
            out.append(
                Shortcut(
                    chord=chord,
                    action=action,
                    description=description,
                    category=infer_category(action, description),
                    source=SourceRef(
                        backend=self.name,
                        path=path,
                        start=offset + indent,
                        end=offset + content_len,
                        line=lineno,
                    ),
                    raw=stripped,
                    extras={
                        "flags": m.group("flags") or "",
                        "eq": m.group("eq"),
                        "seps": seps,
                        "mods_raw": mods_raw,
                        "key_raw": key_raw,
                        "description_raw": description_raw,
                        "dispatcher_raw": dispatcher_raw,
                        "params_raw": params_raw,
                        "had_params": had_params,
                        "submap": submap,
                    },
                )
            )
            offset += len(line)
        return out

    # --- writing -----------------------------------------------------------

    def render(
        self,
        chord: Chord,
        action: str,
        description: str = "",
        extras: dict | None = None,
    ) -> str:
        extras = extras or {}
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
        last_end = 0
        first_submap: int | None = None
        offset = 0
        submap = ""
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            sub = _SUBMAP_RE.match(stripped)
            if sub:
                name = sub.group("name")
                submap = "" if name == "reset" else name
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
        paths = self.config_paths()
        target = paths[0] if paths else (self._root / "hyprland.conf")
        return FloatRule(
            backend=self.name, path=target, body=body, marker=f"# {RULE_MARKER}:"
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
        stripped = line.strip()
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
