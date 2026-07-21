"""Cross-reference index over all parsed files, language-agnostic."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .model import FileInfo
from .parser import tail_name


@dataclass
class RepoIndex:
    """Lookup tables mapping names to the files that define/declare them."""

    files: dict[Path, FileInfo] = field(default_factory=dict)
    class_def_map: dict[str, list[Path]] = field(default_factory=dict)
    class_decl_map: dict[str, list[Path]] = field(default_factory=dict)
    fn_def_map: dict[str, list[Path]] = field(default_factory=dict)
    fn_decl_map: dict[str, list[Path]] = field(default_factory=dict)
    # unqualified name -> known definitions/declarations, so calls like
    # `x.size()` can resolve to `Widget::size`
    fn_def_tail: dict[str, list[tuple[str, Path]]] = field(default_factory=dict)
    fn_decl_tail: dict[str, list[Path]] = field(default_factory=dict)
    filename_index: dict[str, list[Path]] = field(default_factory=dict)
    # reverse references: who calls / uses / derives from / includes a name
    fn_call_sites: dict[str, list[tuple[str, Path]]] = field(default_factory=dict)
    class_use_sites: dict[str, list[tuple[str, Path]]] = field(default_factory=dict)
    derived_map: dict[str, list[str]] = field(default_factory=dict)
    included_by: dict[Path, list[Path]] = field(default_factory=dict)

    @classmethod
    def build(cls, files: dict[Path, FileInfo]) -> "RepoIndex":
        index = cls(
            files=files,
            class_def_map=defaultdict(list),
            class_decl_map=defaultdict(list),
            fn_def_map=defaultdict(list),
            fn_decl_map=defaultdict(list),
            fn_def_tail=defaultdict(list),
            fn_decl_tail=defaultdict(list),
            filename_index=defaultdict(list),
            fn_call_sites=defaultdict(list),
            class_use_sites=defaultdict(list),
            derived_map=defaultdict(list),
            included_by=defaultdict(list),
        )
        for path, info in files.items():
            for d in info.class_defs:
                index.class_def_map[d.name].append(path)
                for base in d.bases:
                    index.derived_map[base_key(base)].append(d.name)
            for d in info.class_decls:
                index.class_decl_map[d.name].append(path)
            for d in info.fn_defs:
                index.fn_def_map[d.name].append(path)
                index.fn_def_tail[tail_name(d.name)].append((d.name, path))
            for d in info.fn_decls:
                index.fn_decl_map[d.name].append(path)
                index.fn_decl_tail[tail_name(d.name)].append(path)
            for d in info.class_defs + info.fn_defs:
                for callee in d.calls:
                    index.fn_call_sites[tail_name(callee)].append((d.name, path))
                for tname in d.type_uses:
                    index.class_use_sites[tname].append((d.name, path))
            index.filename_index[path.name].append(path)
        # second pass: include resolution needs the completed filename index
        for path, info in files.items():
            for include in info.includes:
                target = index.resolve_include(include, path)
                if target is not None and target != path:
                    index.included_by[target].append(path)
        return index

    def resolve_include(self, include: str, includer: Path) -> Path | None:
        """Map an include/import string to the repo-relative path of a known file."""
        candidates = self.filename_index.get(include.rsplit("/", 1)[-1])
        if not candidates:
            return None
        # quote-style include relative to the including file's directory
        local = Path(os.path.normpath(includer.parent / include))
        if local in candidates:
            return local
        # otherwise match the include path against the tail of known paths
        # (e.g. "mlir/IR/Operation.h" -> "mlir/include/mlir/IR/Operation.h")
        matches = [
            c
            for c in candidates
            if c.as_posix() == include or c.as_posix().endswith("/" + include)
        ]
        if matches:
            return min(matches, key=lambda p: len(p.parts))
        return None

    def resolve_fn_defs(self, name: str) -> list[tuple[str, Path]]:
        """Known definitions of a function name as (definition name, path) pairs."""
        if name in self.fn_def_map:
            return [(name, p) for p in self.fn_def_map[name]]
        return self.fn_def_tail.get(tail_name(name), [])

    def resolve_fn_decls(self, name: str) -> list[Path]:
        if name in self.fn_decl_map:
            return self.fn_decl_map[name]
        return self.fn_decl_tail.get(tail_name(name), [])

    def call_sites(self, name: str) -> list[tuple[str, Path]]:
        """Definitions that call a function, as (caller name, path) pairs."""
        return self.fn_call_sites.get(tail_name(name), [])

    def use_sites(self, name: str) -> list[tuple[str, Path]]:
        """Definitions that reference a class type, as (user name, path) pairs."""
        return self.class_use_sites.get(name, [])

    def derived_classes(self, name: str) -> list[str]:
        return list(dict.fromkeys(self.derived_map.get(name, [])))


def base_key(base: str) -> str:
    """Normalize a base-class reference for lookup: strip templates and scope."""
    return tail_name(re.sub(r"<.*", "", base))
