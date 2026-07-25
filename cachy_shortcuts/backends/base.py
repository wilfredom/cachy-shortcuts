"""Backend interface.

A backend is the *only* place that knows a config file format. It turns text
into ``Shortcut`` records (carrying the exact span each binding occupies) and
renders a ``Shortcut`` back into that format.

Offsets throughout are character offsets into the decoded (UTF-8) text, not
raw byte offsets. Files are read and written whole as ``str``, so character
offsets are what slicing actually needs; calling them bytes would be a lie for
any config containing non-ASCII.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..model import Chord, Shortcut


@dataclass
class ParsedFile:
    path: Path
    text: str
    shortcuts: list[Shortcut]


@dataclass(frozen=True)
class FloatRule:
    """A rule that exempts the overlay from a compositor's tiling layout.

    The overlay is normally a layer-shell surface, which no tiler lays out at
    all. This is the safety net for when gtk4-layer-shell is missing or the
    compositor doesn't implement wlr-layer-shell: without it the overlay opens
    as an ordinary toplevel and gets tiled into a column like any other window.

    ``marker`` is a substring whose presence in the file means the rule is
    already installed, which is what makes installation idempotent.
    """

    backend: str
    path: Path
    body: str
    marker: str
    # "append" adds the rule at the end of the file; "ron-list" splices it into
    # an existing ``[ ... ]`` RON list, creating the list if the file is absent.
    mode: str = "append"

    def apply(self, text: str) -> str:
        """Return ``text`` with this rule added. Idempotent."""
        if self.marker in text and self.body.strip() in text:
            return text
        if self.mode == "ron-list":
            return self._apply_ron_list(text)
        if self.mode != "append":
            raise ValueError(f"unknown float-rule mode: {self.mode!r}")
        if not text.strip():
            return self.body.rstrip() + "\n"
        return text.rstrip("\n") + "\n\n" + self.body.rstrip() + "\n"

    def _apply_ron_list(self, text: str) -> str:
        close = text.rfind("]")
        if close == -1:
            # No list yet (missing or empty file): write a whole one.
            return "[\n" + self.body.rstrip() + "\n]\n"
        head = text[:close].rstrip()
        # An empty list is "[" with nothing after it; anything else already has
        # entries, which RON requires us to keep comma-separated.
        if not head.endswith("[") and not head.endswith(","):
            head += ","
        return head + "\n" + self.body.rstrip() + "\n" + text[close:]

    def installed_in(self, text: str) -> bool:
        return self.marker in text and self.body.strip() in text


class Backend(ABC):
    """One compositor's config format."""

    name: str = ""
    display_name: str = ""

    # --- discovery ---------------------------------------------------------

    @abstractmethod
    def config_paths(self) -> list[Path]:
        """Every config file that may contain bindings, includes resolved.

        Ordered so that the file a new binding should be added to comes first.
        """

    def is_installed(self) -> bool:
        """Whether this compositor appears to exist on the system at all."""
        return bool(shutil.which(self.name)) or any(
            p.exists() for p in self.config_paths()
        )

    # --- reading -----------------------------------------------------------

    @abstractmethod
    def parse(self, text: str, path: Path) -> list[Shortcut]:
        """Parse one config file's text. Pure -- no I/O, so it's testable."""

    def read(self) -> list[Shortcut]:
        out: list[Shortcut] = []
        for path in self.config_paths():
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            out.extend(self.parse(text, path))
        return out

    def read_files(self) -> list[ParsedFile]:
        files: list[ParsedFile] = []
        for path in self.config_paths():
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            files.append(ParsedFile(path, text, self.parse(text, path)))
        return files

    # --- writing -----------------------------------------------------------

    @abstractmethod
    def render(
        self,
        chord: Chord,
        action: str,
        description: str = "",
        extras: dict | None = None,
    ) -> str:
        """Render one binding in this format, without trailing newline.

        ``extras`` carries the backend-specific properties captured at parse
        time (niri's ``allow-when-locked``, mango's bind flags, ...). Passing
        them back in is what stops an edit to a chord from quietly discarding
        the rest of the binding.
        """

    @abstractmethod
    def insertion_point(self, text: str) -> tuple[int, str, str]:
        """Where a new binding goes in ``text``.

        Returns ``(offset, prefix, suffix)`` -- the rendered binding is
        inserted at ``offset`` wrapped in prefix/suffix so indentation and
        blank lines come out right.
        """

    # --- tiling exception ---------------------------------------------------

    def float_rule(self) -> FloatRule | None:
        """The rule that keeps the overlay out of this compositor's layout.

        None means this compositor has no such concept (or needs none).
        """
        return None

    # --- runtime -----------------------------------------------------------

    def reload(self) -> None:
        """Ask the compositor to re-read its config. Default: nothing to do."""

    def focused_window(self) -> str | None:
        """app_id of the window focused before the overlay opened, if knowable."""
        return None

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _run(cmd: list[str], timeout: float = 2.0) -> str | None:
        """Run a helper command, returning stdout or None on any failure."""
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env={**os.environ},
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout
