"""Language-agnostic data model for extracted source facts.

A ``LanguageParser`` produces one ``FileInfo`` per source file; everything
downstream (indexing, cross-linking, markdown rendering) only ever sees
these types.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Definition:
    """A named definition (class or function) with its full source snippet."""

    name: str
    snippet: str
    calls: Counter = field(default_factory=Counter)  # callee name -> call count
    type_uses: list[str] = field(default_factory=list)  # class/type names referenced
    bases: list[str] = field(default_factory=list)  # base classes (class defs only)


@dataclass
class Declaration:
    """A named declaration (function prototype, forward class declaration)."""

    name: str
    snippet: str


@dataclass
class FileInfo:
    """Everything extracted from one source file.

    ``path`` is relative to the scanned repository root; ``includes`` holds
    raw reference strings (e.g. ``#include`` paths or import paths) that the
    index resolves to other files by path-tail matching.
    """

    path: Path
    language: str = "text"  # markdown code-fence tag for snippets
    includes: list[str] = field(default_factory=list)
    class_defs: list[Definition] = field(default_factory=list)
    class_decls: list[Declaration] = field(default_factory=list)
    fn_defs: list[Definition] = field(default_factory=list)
    fn_decls: list[Declaration] = field(default_factory=list)
    calls: Counter = field(default_factory=Counter)  # file-wide callee -> count
