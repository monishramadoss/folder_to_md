"""The parser interface: implement this to plug a new language in."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from .model import FileInfo

# scope separators recognized in qualified names ("Widget::size", "Widget.size")
_SCOPE_RE = re.compile(r"::|\.")


def tail_name(name: str) -> str:
    """Unqualified last component of a name: ``Widget::size`` -> ``size``."""
    return _SCOPE_RE.split(name)[-1]


def qualifier(name: str) -> str | None:
    """Scope part of a qualified name, template args stripped, else None."""
    for sep in ("::", "."):
        if sep in name:
            return re.sub(r"<.*", "", name.rsplit(sep, 1)[0])
    return None


class LanguageParser(ABC):
    """Extracts a :class:`FileInfo` from a single source file.

    To support a new language, subclass this with:

    - ``extensions``: the file suffixes the parser handles;
    - ``parse``: fill a ``FileInfo`` with definitions, declarations,
      includes/imports, and call counts.

    Qualified names should use ``::`` or ``.`` as scope separators so the
    index can match unqualified references (see :func:`tail_name`).
    """

    extensions: ClassVar[frozenset[str]] = frozenset()

    def handles(self, path: Path) -> bool:
        return path.suffix in self.extensions

    @abstractmethod
    def parse(self, rel_path: Path, source: bytes) -> FileInfo:
        """Parse ``source`` into a ``FileInfo`` for repo-relative ``rel_path``."""
