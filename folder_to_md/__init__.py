"""folder_to_md: turn a folder of source code into cross-linked markdown.

Pipeline: scan files -> ``LanguageParser.parse`` -> ``RepoIndex.build``
-> ``MarkdownWriter.write_all``. To support a new language, implement
:class:`LanguageParser` and pass an instance to :func:`generate`.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Sequence

import tqdm

from .index import RepoIndex
from .markdown import MarkdownWriter
from .model import Declaration, Definition, FileInfo
from .parser import LanguageParser

__all__ = [
    "Declaration",
    "Definition",
    "FileInfo",
    "LanguageParser",
    "RepoIndex",
    "MarkdownWriter",
    "scan",
    "generate",
]

# per-worker-process parsers, created once by _init_scan_worker
_WORKER_PARSERS: list[LanguageParser] = []


def _init_scan_worker(parser_types: tuple[type[LanguageParser], ...]):
    global _WORKER_PARSERS
    _WORKER_PARSERS = [t() for t in parser_types]


def _scan_one(target: tuple[Path, str, int]) -> FileInfo:
    rel_path, abs_path, parser_idx = target
    return _WORKER_PARSERS[parser_idx].parse(rel_path, Path(abs_path).read_bytes())


def scan(
    root: Path,
    parsers: Sequence[LanguageParser],
    subdirs: str | Sequence[str] = None,
    workers: int = None,
    excludes: Sequence[str] = (),
) -> dict[Path, FileInfo]:
    """Parse every supported file under ``root`` (or the given ``subdirs``).

    ``subdirs`` may be a single subdirectory name or a list of them (e.g.
    ``["mlir", "llvm"]``); files found under more than one are parsed once.
    Paths in the result are relative to ``root``, so links can span the
    whole repository even when only subdirectories are scanned.

    ``excludes`` lists directory names to skip anywhere in the tree (e.g.
    ``["build"]``); dot-directories such as ``.git`` are always skipped.

    ``workers`` controls parallelism (default: CPU count; 1 = sequential).
    Parser objects usually hold unpicklable state (e.g. tree-sitter), so
    worker processes re-instantiate them from their classes — parsers must
    therefore be constructible with no arguments to run in parallel.
    """
    if isinstance(subdirs, str):
        subdirs = [subdirs]
    scan_roots = [root / s for s in subdirs] if subdirs else [root]
    excluded = set(excludes)
    targets, seen = [], set()
    for scan_root in scan_roots:
        for path in scan_root.rglob("*"):
            if not path.is_file():
                continue
            idx = next((i for i, p in enumerate(parsers) if p.handles(path)), None)
            if idx is None:
                continue
            rel_path = path.relative_to(root)
            dirs = rel_path.parts[:-1]
            if any(d in excluded or d.startswith(".") for d in dirs):
                continue
            if rel_path not in seen:
                seen.add(rel_path)
                targets.append((rel_path, str(path), idx))

    workers = workers or os.cpu_count() or 1
    if workers == 1 or len(targets) < 2:
        infos = [
            parsers[i].parse(rel, Path(abs_path).read_bytes())
            for rel, abs_path, i in tqdm.tqdm(targets, desc="parsing")
        ]
    else:
        parser_types = tuple(type(p) for p in parsers)
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_scan_worker,
            initargs=(parser_types,),
        ) as pool:
            infos = list(
                tqdm.tqdm(
                    pool.map(_scan_one, targets, chunksize=16),
                    total=len(targets),
                    desc="parsing",
                )
            )
    return {info.path: info for info in infos}


def generate(
    root: Path,
    output_dir: Path,
    parsers: Sequence[LanguageParser],
    subdirs: str | Sequence[str] = None,
    workers: int = None,
    excludes: Sequence[str] = (),
    index_repo: bool = True,
) -> RepoIndex:
    """Scan ``root``, build the cross-reference index, and write markdown.

    ``subdirs`` selects which files get markdown pages. With ``index_repo``
    (the default) the *whole* repository is still parsed for lookup, so
    pages in the subdirs resolve references into the rest of the codebase —
    those references render as plain paths rather than links, and no pages
    are written for them. Set ``index_repo=False`` to restrict parsing to
    the subdirs as well (references outside them then go unresolved).
    """
    scan_subdirs = None if index_repo else subdirs
    files = scan(root, parsers, scan_subdirs, workers=workers, excludes=excludes)
    index = RepoIndex.build(files)
    pages = None
    if subdirs and index_repo:
        subs = [subdirs] if isinstance(subdirs, str) else list(subdirs)
        pages = {p for p in files if any(p.is_relative_to(s) for s in subs)}
    MarkdownWriter(index, output_dir, pages).write_all(workers=workers)
    return index
