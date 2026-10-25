"""Reading Hyprland's Lua config: a token scanner, and a sandboxed evaluator.

Hyprland 0.55+ loads ``hyprland.lua``, and the CachyOS profile writes its binds
as ordinary Lua::

    local mainMod = "SUPER"
    hl.bind(mainMod .. " + Q", hl.dsp.window.close())
    hl.bind(mainMod .. " + Return", hl.dsp.exec_cmd(launchPrefix .. TERMINAL))
    for i = 1, NUM_WPM do
        hl.bind(mainMod .. " + ALT + " .. digitCode(i % 10), hl.dsp.focus({ workspace = i }))
    end

Two tools, because each answers a different question:

* The **scanner** (``tokenize`` / ``find_sites``) is a scanner, not a Lua
  parser -- the same trade ``_kdl.py`` makes. It finds every ``hl.bind(...)``
  call and the exact character span of the call and of each argument, which
  is what a surgical edit needs. It folds string constants (literals, ``..``,
  names assigned a string once) so ``mainMod .. " + Q"`` reads as ``SUPER + Q``.
* The **evaluator** (``evaluate``) runs the config under ``lua`` with ``hl``
  stubbed out, and reports every bind that actually executed. That is the only
  way to see binds a loop or helper function generates. It cannot give spans, so
  those binds are listed but never edited.

The evaluator executes the user's own config. It is confined: no ``io``, only
``os.getenv/time/date/clock``, ``require`` limited to the config directory,
a timeout, and every ``hl.*`` call is a recording no-op.
"""

from __future__ import annotations

import bisect
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# --- tokens ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Tok:
    kind: str  # name | str | num | op
    text: str  # str tokens hold the decoded value
    start: int
    end: int


_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUM_RE = re.compile(
    r"0[xX][0-9a-fA-F]*(?:\.[0-9a-fA-F]*)?(?:[pP][+-]?\d+)?"
    r"|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
)
_LONG_OPEN_RE = re.compile(r"\[(=*)\[")
_OPS2 = ("..", "==", "~=", "<=", ">=", "//", "<<", ">>", "::")
_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
    "\\": "\\", '"': '"', "'": "'", "\n": "\n",
}  # fmt: skip
_OPEN = frozenset("({[")
_CLOSE = frozenset(")}]")


def tokenize(text: str) -> list[Tok]:
    """Lua source as tokens, comments and whitespace dropped.

    Lenient: an unterminated string or comment runs to the end of its line or
    file rather than raising, since the file being scanned is the user's.
    """
    toks: list[Tok] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n\f\v":
            i += 1
            continue
        if text.startswith("--", i):
            m = _LONG_OPEN_RE.match(text, i + 2)
            if m:
                close = "]" + m.group(1) + "]"
                j = text.find(close, m.end())
                i = n if j == -1 else j + len(close)
            else:
                j = text.find("\n", i)
                i = n if j == -1 else j
            continue
        if c in "\"'":
            value, j = _short_string(text, i)
            toks.append(Tok("str", value, i, j))
            i = j
            continue
        if c == "[":
            m = _LONG_OPEN_RE.match(text, i)
            if m:
                close = "]" + m.group(1) + "]"
                j = text.find(close, m.end())
                body_start = m.end() + (1 if text.startswith("\n", m.end()) else 0)
                body = text[body_start : n if j == -1 else j]
                end = n if j == -1 else j + len(close)
                toks.append(Tok("str", body, i, end))
                i = end
                continue
        m = _NAME_RE.match(text, i)
        if m:
            toks.append(Tok("name", m.group(), i, m.end()))
            i = m.end()
            continue
        if c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
            m = _NUM_RE.match(text, i)
            if m:
                toks.append(Tok("num", m.group(), i, m.end()))
                i = m.end()
                continue
        if text.startswith("...", i):
            toks.append(Tok("op", "...", i, i + 3))
            i += 3
            continue
        two = text[i : i + 2]
        if two in _OPS2:
            toks.append(Tok("op", two, i, i + 2))
            i += 2
            continue
        toks.append(Tok("op", c, i, i + 1))
        i += 1
    return toks


def _short_string(text: str, i: int) -> tuple[str, int]:
    """The decoded value and end offset of the quoted string at ``text[i]``."""
    quote, n = text[i], len(text)
    j, buf = i + 1, []
    while j < n and text[j] != quote and text[j] != "\n":
        c = text[j]
        if c != "\\" or j + 1 >= n:
            buf.append(c)
            j += 1
            continue
        e = text[j + 1]
        if e in _ESCAPES:
            buf.append(_ESCAPES[e])
            j += 2
        elif e == "x" and re.match(r"[0-9a-fA-F]{2}", text[j + 2 : j + 4]):
            buf.append(chr(int(text[j + 2 : j + 4], 16)))
            j += 4
        elif e.isdigit():
            m = re.match(r"\d{1,3}", text[j + 1 :])
            buf.append(chr(min(int(m.group()), 255)))
            j += 1 + len(m.group())
        elif e == "z":
            j += 2
            while j < n and text[j] in " \t\r\n\f\v":
                j += 1
        elif e == "u" and text.startswith("{", j + 2) and "}" in text[j:]:
            close = text.index("}", j)
            try:
                buf.append(chr(int(text[j + 3 : close], 16)))
            except ValueError:
                pass
            j = close + 1
        else:
            buf.append(e)
            j += 2
    return "".join(buf), (j + 1 if j < n and text[j] == quote else j)


def _op(tok: Tok, text: str) -> bool:
    return tok.kind == "op" and tok.text == text


def _name(tok: Tok, text: str) -> bool:
    return tok.kind == "name" and tok.text == text


# --- blocks and brackets -----------------------------------------------------


class _Blocks:
    """The ``function / if / for / do / repeat`` nesting at a point in a file.

    Only needed to know whether a call sits at the top level, in a loop, or in
    a helper function -- and to keep a comma inside a function body from being
    read as the separator between a call's arguments.
    """

    def __init__(self) -> None:
        self.stack: list[tuple[str, int]] = []
        self._pending_do = 0

    def feed(self, tok: Tok) -> None:
        if tok.kind != "name":
            return
        word = tok.text
        if word in ("for", "while"):
            self.stack.append(("loop", tok.start))
            self._pending_do += 1
        elif word == "do":
            if self._pending_do:
                self._pending_do -= 1
            else:
                self.stack.append(("do", tok.start))
        elif word in ("if", "repeat"):
            self.stack.append((word, tok.start))
        elif word == "function":
            self.stack.append(("function", tok.start))
        elif word in ("end", "until") and self.stack:
            self.stack.pop()


def _match(toks: list[Tok], i: int) -> int | None:
    """Index of the bracket closing the one at ``toks[i]``."""
    depth = 0
    for j in range(i, len(toks)):
        t = toks[j]
        if t.kind != "op":
            continue
        if t.text in _OPEN:
            depth += 1
        elif t.text in _CLOSE:
            depth -= 1
            if depth == 0:
                return j
    return None


def _split_top(
    toks: list[Tok], a: int, b: int, seps: frozenset[str]
) -> list[tuple[int, int]]:
    """Token ranges of the comma-separated items in ``toks[a:b]``."""
    out: list[tuple[int, int]] = []
    depth, start, blocks = 0, a, _Blocks()
    for j in range(a, b):
        t = toks[j]
        if t.kind == "op":
            if t.text in _OPEN:
                depth += 1
            elif t.text in _CLOSE:
                depth -= 1
            elif t.text in seps and depth == 0 and not blocks.stack:
                out.append((start, j))
                start = j + 1
                continue
        blocks.feed(t)
    if start < b:
        out.append((start, b))
    return out


# --- folding string constants ------------------------------------------------

_SUFFIX_OPS = frozenset(
    ["(", ".", ":", "[", "{", ",", "+", "-", "*", "/", "%", "^", "//", "<<", ">>",
     "==", "~=", "<", ">", "<=", ">=", "&", "|", "~", "#"]
)  # fmt: skip
_KEYWORDS = frozenset(
    "and break do else elseif end false for function goto if in local nil not or "
    "repeat return then true until while".split()
)


class _NoFold(Exception):
    pass


def _fold_prefix(
    toks: list[Tok], pos: int, consts: dict[str, str]
) -> tuple[str, int] | None:
    """Fold ``term (.. term)*`` starting at ``pos``: (value, next position).

    A term is a string, number, known constant or parenthesised expression --
    and nothing that is then called, indexed or used in arithmetic, since that
    makes it something other than the plain value it looked like.
    """

    def term(p: int) -> tuple[str, int]:
        if p >= len(toks):
            raise _NoFold
        t = toks[p]
        if t.kind in ("str", "num"):
            value, p = t.text, p + 1
        elif t.kind == "name" and t.text in consts:
            value, p = consts[t.text], p + 1
        elif _op(t, "("):
            value, p = expr(p + 1)
            if p >= len(toks) or not _op(toks[p], ")"):
                raise _NoFold
            p += 1
        else:
            raise _NoFold
        nxt = toks[p] if p < len(toks) else None
        if nxt is not None and nxt.kind == "str":
            raise _NoFold  # f "x" is a call
        if nxt is not None and nxt.kind == "op" and nxt.text in _SUFFIX_OPS:
            raise _NoFold
        return value, p

    def expr(p: int) -> tuple[str, int]:
        value, p = term(p)
        while p < len(toks) and _op(toks[p], ".."):
            more, p = term(p + 1)
            value += more
        return value, p

    try:
        return expr(pos)
    except _NoFold:
        return None


def fold(toks: list[Tok], consts: dict[str, str]) -> str | None:
    """The string a whole token slice evaluates to, or None if it isn't constant."""
    if not toks:
        return None
    got = _fold_prefix(toks, 0, consts)
    if got is None or got[1] != len(toks):
        return None
    return got[0]


def _simple_rhs_end(toks: list[Tok], pos: int) -> int | None:
    """End of a ``term (.. term)*`` right-hand side, structurally (names unresolved)."""

    def term(p: int) -> int:
        if p >= len(toks):
            raise _NoFold
        t = toks[p]
        if t.kind in ("str", "num") or (t.kind == "name" and t.text not in _KEYWORDS):
            p += 1
        elif _op(t, "("):
            p = expr(p + 1)
            if p >= len(toks) or not _op(toks[p], ")"):
                raise _NoFold
            p += 1
        else:
            raise _NoFold
        nxt = toks[p] if p < len(toks) else None
        if nxt is not None and (
            nxt.kind == "str" or (nxt.kind == "op" and nxt.text in _SUFFIX_OPS)
        ):
            raise _NoFold
        return p

    def expr(p: int) -> int:
        p = term(p)
        while p < len(toks) and _op(toks[p], ".."):
            p = term(p + 1)
        return p

    try:
        return expr(pos)
    except _NoFold:
        return None


def constants(token_lists: list[list[Tok]]) -> dict[str, str]:
    """Names that stand for one fixed string across the whole config.

    A name counts only if every statement-level assignment to it, in every
    file, folds to the same string -- and it is never also a loop variable or
    function parameter somewhere, which would let a ``for i`` read as the
    ``local i = 1`` beside it. Anything doubtful is left out: a bind whose keys
    then don't fold is listed read-only from the evaluator instead.
    """
    assigns: dict[str, list[list[Tok] | None]] = {}
    shadowed: set[str] = set()
    for toks in token_lists:
        depth = 0
        for i, t in enumerate(toks):
            if t.kind == "op":
                if t.text in _OPEN:
                    depth += 1
                elif t.text in _CLOSE:
                    depth -= 1
                continue
            if t.kind != "name":
                continue
            if t.text == "for":
                j = i + 1
                while j < len(toks) and not (
                    _op(toks[j], "=") or _name(toks[j], "in")
                ):
                    if toks[j].kind == "name":
                        shadowed.add(toks[j].text)
                    j += 1
            elif t.text == "function":
                j = i + 1
                while j < len(toks) and not _op(toks[j], "("):
                    j += 1
                while j + 1 < len(toks) and not _op(toks[j], ")"):
                    j += 1
                    if toks[j].kind == "name":
                        shadowed.add(toks[j].text)
            elif (
                depth == 0
                and t.text not in _KEYWORDS
                and i + 1 < len(toks)
                and _op(toks[i + 1], "=")
                and not (i and toks[i - 1].kind == "op" and toks[i - 1].text in ".:,")
            ):
                end = _simple_rhs_end(toks, i + 2)
                assigns.setdefault(t.text, []).append(
                    None if end is None else toks[i + 2 : end]
                )
    consts: dict[str, str] = {}
    for _ in range(8):
        changed = False
        for name, rhss in assigns.items():
            if name in shadowed or name in consts:
                continue
            values = {None if r is None else fold(r, consts) for r in rhss}
            if len(values) == 1 and None not in values:
                consts[name] = values.pop()
                changed = True
        if not changed:
            break
    return consts


# --- finding the calls -------------------------------------------------------


@dataclass
class Site:
    """One ``hl.bind`` / ``hl.unbind`` / ``require`` call in a file."""

    kind: str  # bind | unbind | require
    start: int  # the whole call, as character offsets
    end: int
    line: int
    end_line: int
    toks: tuple[int, int]  # token index range of the whole call
    args: list[tuple[int, int]] = field(default_factory=list)  # token ranges
    keys: str | None = None  # folded first argument
    module: str | None = None  # require's argument
    submap: str = ""
    top: bool = False  # at file level: not inside any block
    generated: bool = False  # inside a loop or helper function


def find_sites(text: str, toks: list[Tok], consts: dict[str, str]) -> list[Site]:
    """Every bind, unbind and require call in ``text``, in source order."""
    line_starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def line_of(offset: int) -> int:
        return bisect.bisect_right(line_starts, offset)

    sites: list[Site] = []
    spans: list[tuple[int, int, str]] = []  # define_submap call spans
    blocks = _Blocks()
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        if t.kind != "name":
            i += 1
            continue
        after_dot = i > 0 and toks[i - 1].kind == "op" and toks[i - 1].text in ".:"
        if (
            t.text == "hl"
            and not after_dot
            and i + 3 < n
            and _op(toks[i + 1], ".")
            and toks[i + 2].kind == "name"
            and toks[i + 2].text in ("bind", "unbind", "define_submap")
            and _op(toks[i + 3], "(")
        ):
            close = _match(toks, i + 3)
            if close is not None:
                which = toks[i + 2].text
                args = _split_top(toks, i + 4, close, frozenset(","))
                first = fold(toks[args[0][0] : args[0][1]], consts) if args else None
                if which == "define_submap":
                    spans.append((toks[i + 3].start, toks[close].end, first or "?"))
                    blocks.feed(t)
                    i += 1
                    continue
                if args and all(b > a for a, b in args):
                    sites.append(
                        _site(which, text, toks, i, close, args, first, spans, blocks, line_of)
                    )
                i = close + 1
                continue
        if t.text == "require" and not after_dot and i + 1 < n:
            module = end = None
            if toks[i + 1].kind == "str":
                module, end = toks[i + 1].text, i + 1
            elif (
                i + 3 < n
                and _op(toks[i + 1], "(")
                and toks[i + 2].kind == "str"
                and _op(toks[i + 3], ")")
            ):
                module, end = toks[i + 2].text, i + 3
            if module is not None:
                site = _site("require", text, toks, i, end, [], None, spans, blocks, line_of)
                site.module = module
                sites.append(site)
                i = end + 1
                continue
        blocks.feed(t)
        i += 1
    return sites


def _site(kind, text, toks, i, close, args, first, spans, blocks, line_of) -> Site:
    start, end = toks[i].start, toks[close].end
    submap = ""
    for lo, hi, name in reversed(spans):
        if lo <= start < hi:
            submap = name
            break
    funcs = [s for k, s in blocks.stack if k == "function"]
    if funcs and any(lo <= funcs[0] < hi for lo, hi, _ in spans):
        funcs = funcs[1:]  # the function define_submap itself was handed
    generated = bool(funcs) or any(k in ("loop", "repeat") for k, _ in blocks.stack)
    return Site(
        kind=kind,
        start=start,
        end=end,
        line=line_of(start),
        end_line=line_of(max(start, end - 1)),
        toks=(i, close + 1),
        args=list(args),
        keys=first if kind != "require" else None,
        submap=submap,
        top=not blocks.stack,
        generated=generated,
    )


def span(toks: list[Tok], rng: tuple[int, int]) -> tuple[int, int]:
    """Character span of a token range."""
    return toks[rng[0]].start, toks[rng[1] - 1].end


def required_modules(toks: list[Tok]) -> list[str]:
    """Module names ``require``d at file level, in order."""
    return [s.module for s in find_sites("", toks, {}) if s.kind == "require" and not s.generated and s.module]


def resolve_module(root: Path, name: str) -> Path | None:
    """``require("a.b")`` -> ``root/a/b.lua`` or ``root/a/b/init.lua``, inside ``root``."""
    if not name or ".." in name or "/" in name or "\\" in name:
        return None
    rel = name.replace(".", "/")
    for candidate in (root / f"{rel}.lua", root / rel / "init.lua"):
        if candidate.is_file():
            return candidate
    return None


@dataclass
class Field:
    """One entry of a table constructor, as token ranges."""

    key: str | None
    item: tuple[int, int]
    value: tuple[int, int]


def table_fields(toks: list[Tok], rng: tuple[int, int]) -> list[Field] | None:
    """The entries of the ``{ ... }`` in ``toks[rng]``, or None if it isn't one."""
    a, b = rng
    if b - a < 2 or not _op(toks[a], "{") or _match(toks, a) != b - 1:
        return None
    out: list[Field] = []
    for ia, ib in _split_top(toks, a + 1, b - 1, frozenset(",;")):
        key = None
        value = (ia, ib)
        if ib - ia >= 3 and toks[ia].kind == "name" and _op(toks[ia + 1], "="):
            key, value = toks[ia].text, (ia + 2, ib)
        elif (
            ib - ia >= 5
            and _op(toks[ia], "[")
            and toks[ia + 1].kind == "str"
            and _op(toks[ia + 2], "]")
            and _op(toks[ia + 3], "=")
        ):
            key, value = toks[ia + 1].text, (ia + 4, ib)
        out.append(Field(key, (ia, ib), value))
    return out


def top_level_names(toks: list[Tok]) -> list[str]:
    """Names assigned at file level (not inside any block), in order."""
    out: list[str] = []
    depth, blocks = 0, _Blocks()
    for i, t in enumerate(toks):
        if t.kind == "op":
            if t.text in _OPEN:
                depth += 1
            elif t.text in _CLOSE:
                depth -= 1
            continue
        if t.kind != "name":
            continue
        if (
            depth == 0
            and not blocks.stack
            and t.text not in _KEYWORDS
            and i + 1 < len(toks)
            and _op(toks[i + 1], "=")
            and not (i and toks[i - 1].kind == "op" and toks[i - 1].text in ".:,")
        ):
            out.append(t.text)
        blocks.feed(t)
    return out


def action_text(
    text: str, toks: list[Tok], rng: tuple[int, int], consts: dict[str, str]
) -> str:
    """A bind's dispatcher argument as the action text the rest of the tool reads.

    ``hl.dsp.exec_cmd("kitty")`` is ``exec kitty`` -- the same shape as every
    other backend's spawn, so wrapping, unwrapping and ownership detection need
    no special case. Other dispatchers keep their source, minus ``hl.dsp.``.
    """
    a, b = rng
    seg = toks[a:b]
    if _name(seg[0], "function"):
        return "lua function"
    squash = lambda t: re.sub(r"\s+", " ", t).strip()  # noqa: E731
    if (
        len(seg) > 4
        and _name(seg[0], "hl")
        and _op(seg[1], ".")
        and _name(seg[2], "dsp")
        and _op(seg[3], ".")
    ):
        if (
            _name(seg[4], "exec_cmd")
            and len(seg) > 5
            and _op(seg[5], "(")
            and _match(toks, a + 5) == b - 1
        ):
            inner = _split_top(toks, a + 6, b - 1, frozenset(","))
            if len(inner) == 1:
                command = fold(toks[inner[0][0] : inner[0][1]], consts)
                if command is not None:
                    return f"exec {command}"
        return squash(text[toks[a + 4].start : toks[b - 1].end])
    return squash(text[toks[a].start : toks[b - 1].end])


def flag_names(opts: str | None) -> list[str]:
    """Options set to a literal ``true`` in an options table: ``["locked", ...]``."""
    if not opts:
        return []
    toks = tokenize(opts)
    out = []
    for f in table_fields(toks, (0, len(toks))) or []:
        value = toks[f.value[0] : f.value[1]]
        if f.key and len(value) == 1 and _name(value[0], "true"):
            out.append(f.key)
    return out


def lua_string(value: str) -> str:
    """``value`` as a double-quoted Lua string literal."""
    out = ['"']
    for c in value:
        if c == "\\":
            out.append("\\\\")
        elif c == '"':
            out.append('\\"')
        elif c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        elif c == "\t":
            out.append("\\t")
        elif ord(c) < 32:
            out.append(f"\\{ord(c):03d}")
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


_DESCRIPTION_KEYS = ("description", "desc")


def opts_description(text: str | None, consts: dict[str, str]) -> str:
    """The ``description`` in a ``{ ... }`` options table, or ""."""
    if not text:
        return ""
    toks = tokenize(text)
    fields = table_fields(toks, (0, len(toks)))
    for f in fields or []:
        if f.key in _DESCRIPTION_KEYS:
            return fold(toks[f.value[0] : f.value[1]], consts) or ""
    return ""


def set_opts_description(text: str | None, description: str) -> str | None:
    """``text`` (an options table, or None) with its description set.

    Everything else in the table keeps its spelling. An empty description
    removes the field, and a table left empty is dropped altogether (None).
    Raises ValueError for options that are not a table literal, since a
    variable can't be edited in place.
    """
    if not text:
        return f"{{ description = {lua_string(description)} }}" if description else None
    toks = tokenize(text)
    fields = table_fields(toks, (0, len(toks)))
    if fields is None:
        if description:
            raise ValueError(
                "this binding's options aren't a table literal, so its description can't be set"
            )
        return text
    found = next((f for f in fields if f.key in _DESCRIPTION_KEYS), None)
    if description:
        literal = lua_string(description)
        if found is not None:
            a, b = span(toks, found.value)
            return text[:a] + literal + text[b:]
        if not fields:
            return f"{{ description = {literal} }}"
        _, last_end = span(toks, fields[-1].item)
        return text[:last_end] + f", description = {literal}" + text[last_end:]
    if found is None:
        return text
    if len(fields) == 1:
        return None
    index = fields.index(found)
    a, b = span(toks, found.item)
    if index + 1 < len(fields):
        next_start = toks[fields[index + 1].item[0]].start
        return text[:a] + text[next_start:]
    prev_end = toks[fields[index - 1].item[1] - 1].end
    return text[:prev_end] + text[b:]


# --- evaluator ---------------------------------------------------------------

_LUA_BINARIES = ("lua", "lua5.5", "lua5.4", "luajit")


def find_lua() -> str | None:
    for name in _LUA_BINARIES:
        found = shutil.which(name)
        if found:
            return found
    return None


@dataclass
class EvalResult:
    records: list[dict]
    errors: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def evaluate(entry: Path, root: Path, timeout: float = 4.0) -> EvalResult | None:
    """Run the config under ``lua`` and report the binds it made, in order.

    None when there is no ``lua``, it timed out, or produced nothing usable --
    the caller then falls back to the scanner alone.
    """
    lua = find_lua()
    if lua is None:
        return None
    try:
        proc = subprocess.run(
            [lua, "-", str(root), str(entry)],
            input=_PRELUDE.encode(),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    result = EvalResult(records=[])
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        op = rec.get("op")
        if op in ("bind", "unbind"):
            result.records.append(rec)
        elif op == "error":
            result.errors.append(str(rec.get("msg", "")))
        elif op == "missing":
            result.missing.extend(rec.get("modules", []))
    return result


def lua_literal(value) -> str:
    """A JSON value from the evaluator, back as Lua source."""
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, list):
        return "{ " + ", ".join(lua_literal(v) for v in value) + " }" if value else "{}"
    if isinstance(value, dict):
        if "__dsp" in value:
            return format_dsp(value)
        if value.get("__fn"):
            return "function() end"
        if not value:
            return "{}"
        parts = []
        for k, v in value.items():
            key = k if re.fullmatch(r"[A-Za-z_]\w*", k) and k not in _KEYWORDS else f"[{lua_string(k)}]"
            parts.append(f"{key} = {lua_literal(v)}")
        return "{ " + ", ".join(parts) + " }"
    return "nil"


def format_dsp(node: dict) -> str:
    """An evaluated ``hl.dsp.*`` call as text: ``window.move({ workspace = "m+1" })``."""
    args = ", ".join(lua_literal(a) for a in node.get("args", []))
    return f"{node['__dsp']}({args})"


_PRELUDE = r"""
local root, entry = arg[1], arg[2]
local getinfo, io_open, io_write = debug.getinfo, io.open, io.write
local out, notes = {}, {}

local ESC = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
local function jstr(s)
  return '"' .. (tostring(s):gsub('[%c"\\]', function(c)
    return ESC[c] or string.format('\\u%04x', c:byte())
  end)) .. '"'
end

-- Anything the config touches that this stub doesn't model: callable,
-- indexable, arithmetic-safe, and never an endless sequence for ipairs.
local ANY = {}
local any_mt = {}
any_mt.__index = function(_, k) if type(k) == "number" then return nil end return ANY end
any_mt.__newindex = function() end
any_mt.__call = function() return ANY end
for _, ev in ipairs({ "__add", "__sub", "__mul", "__div", "__mod", "__pow", "__unm", "__idiv", "__concat" }) do
  any_mt[ev] = function() return ANY end
end
any_mt.__lt = function() return false end
any_mt.__le = function() return false end
any_mt.__len = function() return 0 end
any_mt.__tostring = function() return "" end
setmetatable(ANY, any_mt)

local ser
ser = function(v, d)
  local t = type(v)
  if t == "boolean" then return tostring(v) end
  if t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    if math.type(v) == "integer" then return tostring(v) end
    return string.format("%.14g", v)
  end
  if t == "string" then return jstr(v) end
  if t == "function" then return '{"__fn":true}' end
  if t ~= "table" or v == ANY or d > 6 then return "null" end
  if rawget(v, "__dsp") then
    local a = {}
    for i = 1, v.args.n do a[i] = ser(v.args[i], d + 1) end
    return '{"__dsp":' .. jstr(v.__dsp) .. ',"args":[' .. table.concat(a, ",") .. ']}'
  end
  if rawget(v, "__path") then return '{"__fn":true}' end
  local n, count = #v, 0
  for _ in pairs(v) do count = count + 1 end
  if count == 0 then return "[]" end
  local parts = {}
  if n == count then
    for i = 1, n do parts[i] = ser(v[i], d + 1) end
    return "[" .. table.concat(parts, ",") .. "]"
  end
  local keys = {}
  for k in pairs(v) do keys[#keys + 1] = tostring(k) end
  table.sort(keys)
  for _, k in ipairs(keys) do
    local val = v[k]
    if val == nil then val = v[tonumber(k)] end
    parts[#parts + 1] = jstr(k) .. ":" .. ser(val, d + 1)
  end
  return "{" .. table.concat(parts, ",") .. "}"
end

local function dsp_node(path)
  return setmetatable({ __path = path }, {
    __index = function(_, k) return dsp_node(path == "" and tostring(k) or (path .. "." .. tostring(k))) end,
    __call = function(_, ...) return { __dsp = path, args = table.pack(...) } end,
  })
end

local submap = ""
local hl = { dsp = dsp_node("") }

local function where(info)
  info = info or {}
  local src = (info.source or ""):gsub("^@", "")
  return '"file":' .. jstr(src) .. ',"line":' .. tostring(info.currentline or 0)
end

function hl.bind(keys, d, opts)
  local info = getinfo(2, "Sl")
  if type(keys) ~= "string" then return ANY end
  out[#out + 1] = '{"op":"bind","keys":' .. jstr(keys) .. ',"dsp":' .. ser(d, 0)
    .. ',"opts":' .. (type(opts) == "table" and ser(opts, 0) or "null")
    .. ',"submap":' .. jstr(submap) .. ',' .. where(info) .. '}'
  return ANY
end

function hl.unbind(keys)
  local info = getinfo(2, "Sl")
  if type(keys) ~= "string" then return end
  out[#out + 1] = '{"op":"unbind","keys":' .. jstr(keys) .. ',' .. where(info) .. '}'
end

function hl.define_submap(name, a, b)
  local fn = type(a) == "function" and a or b
  local previous = submap
  submap = tostring(name)
  if type(fn) == "function" then
    local ok, err = pcall(fn)
    if not ok then out[#out + 1] = '{"op":"error","msg":' .. jstr(err) .. '}' end
  end
  submap = previous
end

setmetatable(hl, { __index = function() return ANY end })

local env = {}
for _, k in ipairs({ "assert", "error", "getmetatable", "ipairs", "next", "pairs", "pcall",
  "rawequal", "rawget", "rawlen", "rawset", "select", "setmetatable", "tonumber", "tostring",
  "type", "xpcall", "string", "table", "math", "utf8", "coroutine" }) do
  env[k] = _G[k]
end
env._VERSION = _VERSION
env.unpack = table.unpack
env.os = { getenv = os.getenv, time = os.time, date = os.date, clock = os.clock }
env.print = function() end
env._G = env
env.hl = hl
env.load = function(s, name, mode, e) return load(s, name, "t", e or env) end

local loaded = {}
local function exists(p)
  local f = io_open(p, "rb")
  if f then f:close() return true end
  return false
end
local function resolve(name)
  if type(name) ~= "string" or name == "" or name:find("%.%.") or name:find("[/\\]") then return nil end
  local rel = name:gsub("%.", "/")
  for _, p in ipairs({ root .. "/" .. rel .. ".lua", root .. "/" .. rel .. "/init.lua" }) do
    if exists(p) then return p end
  end
end

local function run(p, name)
  local f = io_open(p, "rb")
  local src = f:read("a")
  f:close()
  local chunk, err = load(src, "@" .. p, "t", env)
  if not chunk then
    out[#out + 1] = '{"op":"error","msg":' .. jstr(err) .. '}'
    loaded[name] = ANY
    return ANY
  end
  loaded[name] = true
  local ok, res = pcall(chunk, name, p)
  if not ok then
    out[#out + 1] = '{"op":"error","msg":' .. jstr(res) .. '}'
    return ANY
  end
  if res == nil then res = true end
  loaded[name] = res
  return res
end

env.require = function(name)
  if loaded[name] ~= nil then return loaded[name] end
  local p = resolve(name)
  if not p then
    notes[#notes + 1] = jstr(name)
    loaded[name] = ANY
    return ANY
  end
  return run(p, name)
end

run(entry, "@entry")
if #notes > 0 then out[#out + 1] = '{"op":"missing","modules":[' .. table.concat(notes, ",") .. ']}' end
io_write(table.concat(out, "\n"), "\n")
"""
