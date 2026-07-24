"""Just enough KDL scanning to locate nodes and their spans.

A full KDL parser is unnecessary and would lose what we actually need: the
exact character range each binding occupies, so an edit can replace only those
characters. This scanner understands the syntax that can *hide* a brace --
comments, strings, raw strings -- which is all it takes to match braces
reliably in a real niri config.
"""

from __future__ import annotations


class Scanner:
    def __init__(self, text: str) -> None:
        self.t = text
        self.n = len(text)

    # --- trivia ------------------------------------------------------------

    def skip_trivia(self, i: int, stop_at_newline: bool = False) -> int:
        """Skip whitespace and comments. Does not skip ``/-`` slashdash."""
        while i < self.n:
            c = self.t[i]
            if c == "\n" and stop_at_newline:
                return i
            if c.isspace():
                i += 1
                continue
            if self.t.startswith("//", i):
                nl = self.t.find("\n", i)
                i = self.n if nl == -1 else nl
                continue
            if self.t.startswith("/*", i):
                i = self._skip_block_comment(i)
                continue
            if c == "\\" and i + 1 < self.n:
                # KDL line continuation
                j = i + 1
                while j < self.n and self.t[j] in " \t":
                    j += 1
                if j < self.n and self.t[j] == "\n":
                    i = j + 1
                    continue
            return i
        return i

    def _skip_block_comment(self, i: int) -> int:
        # KDL block comments nest.
        depth = 0
        while i < self.n:
            if self.t.startswith("/*", i):
                depth += 1
                i += 2
            elif self.t.startswith("*/", i):
                depth -= 1
                i += 2
                if depth == 0:
                    return i
            else:
                i += 1
        return self.n

    # --- strings -----------------------------------------------------------

    def skip_string(self, i: int) -> int:
        """``i`` is at a quote or an ``r``-prefixed raw string. Returns index after."""
        if self.t[i] == "r":
            j = i + 1
            hashes = 0
            while j < self.n and self.t[j] == "#":
                hashes += 1
                j += 1
            if j < self.n and self.t[j] == '"':
                terminator = '"' + "#" * hashes
                end = self.t.find(terminator, j + 1)
                return self.n if end == -1 else end + len(terminator)
            return i + 1
        if self.t[i] != '"':
            return i + 1
        i += 1
        while i < self.n:
            if self.t[i] == "\\":
                i += 2
                continue
            if self.t[i] == '"':
                return i + 1
            i += 1
        return self.n

    def at_string(self, i: int) -> bool:
        if i >= self.n:
            return False
        if self.t[i] == '"':
            return True
        if self.t[i] == "r":
            j = i + 1
            while j < self.n and self.t[j] == "#":
                j += 1
            return j < self.n and self.t[j] == '"'
        return False

    # --- structure ---------------------------------------------------------

    def match_brace(self, i: int) -> int:
        """``i`` is at ``{``. Returns the index just after the matching ``}``."""
        assert self.t[i] == "{"
        depth = 0
        while i < self.n:
            if self.at_string(i):
                i = self.skip_string(i)
                continue
            if self.t.startswith("//", i) or self.t.startswith("/*", i):
                before = i
                i = self.skip_trivia(i)
                if i == before:
                    i += 1
                continue
            c = self.t[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return self.n

    def line_of(self, offset: int) -> int:
        return self.t.count("\n", 0, offset) + 1


def find_block(text: str, name: str) -> tuple[int, int, int] | None:
    """Locate a top-level ``name { ... }`` node.

    Returns ``(body_start, body_end, close_brace_index)`` where the body is the
    text strictly between the braces, or None if the node is absent.
    """
    sc = Scanner(text)
    i = 0
    while i < sc.n:
        i = sc.skip_trivia(i)
        if i >= sc.n:
            break
        if sc.at_string(i):
            i = sc.skip_string(i)
            continue
        # Read an identifier.
        start = i
        while i < sc.n and not text[i].isspace() and text[i] not in "{};":
            i += 1
        ident = text[start:i]
        j = sc.skip_trivia(i)
        if j < sc.n and text[j] == "{":
            end = sc.match_brace(j)
            if ident == name:
                return (j + 1, end - 1, end - 1)
            i = end
            continue
        # Not a block node; skip to end of this node.
        while i < sc.n and text[i] not in "\n;":
            if sc.at_string(i):
                i = sc.skip_string(i)
                continue
            if text[i] == "{":
                i = sc.match_brace(i)
                continue
            i += 1
        i += 1
    return None
