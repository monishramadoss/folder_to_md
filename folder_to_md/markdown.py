"""Renders one markdown page per parsed file, cross-linked via the index."""

from __future__ import annotations

import os
import re
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

import tqdm

from .index import RepoIndex
from .model import FileInfo
from .parser import qualifier

# per-worker-process writer, created once by _init_write_worker
_WORKER_WRITER: "MarkdownWriter" = None


def _init_write_worker(index: RepoIndex, output_dir: Path, pages: set[Path] | None):
    global _WORKER_WRITER
    _WORKER_WRITER = MarkdownWriter(index, output_dir, pages)


def _write_one(task: tuple[Path, FileInfo]):
    # the page's own FileInfo rides along with the task; the worker's index
    # holds only the cross-reference maps (see write_all)
    path, info = task
    _WORKER_WRITER.write_page(path, info)


def md_anchor(name: str) -> str:
    # GitHub-style heading slug: lowercase, drop anything but word chars and dashes
    return re.sub(r"[^a-z0-9_-]", "", name.lower().replace(" ", "-"))


def _mermaid_id(name: str) -> str:
    return re.sub(r"\W", "_", name) or "_"


def _mermaid_label(name: str) -> str:
    # mermaid parses < > as HTML inside labels; use entity escapes
    return name.replace("<", "#lt;").replace(">", "#gt;").replace('"', "#quot;")


def class_hierarchy_diagram(name: str, bases: list[str],
                            derived: list[str]) -> list[str] | None:
    """Mermaid classDiagram of direct bases and known derived classes."""
    if not bases and not derived:
        return None
    lines = ["\n```mermaid\nclassDiagram\n"]
    for base in bases:
        lines.append(f"    {_mermaid_id(base)} <|-- {_mermaid_id(name)}\n")
    for sub in derived:
        lines.append(f"    {_mermaid_id(name)} <|-- {_mermaid_id(sub)}\n")
    lines.append("```\n")
    return lines


def call_graph_diagram(name: str, callers: list[str],
                       callees: list[str]) -> list[str] | None:
    """Mermaid flowchart: callers --> this function --> callees."""
    if not callers and not callees:
        return None
    fid = f"fn_{_mermaid_id(name)}"
    lines = ["\n```mermaid\nflowchart LR\n", f'    {fid}["{_mermaid_label(name)}"]\n']
    for i, caller in enumerate(callers):
        lines.append(f'    in{i}["{_mermaid_label(caller)}"] --> {fid}\n')
    for i, callee in enumerate(callees):
        lines.append(f'    {fid} --> out{i}["{_mermaid_label(callee)}"]\n')
    lines.append("```\n")
    return lines


@dataclass
class MarkdownWriter:
    index: RepoIndex
    output_dir: Path
    # files that get a markdown page; None = every indexed file. References
    # to indexed files outside this set render as plain paths, not links.
    pages: set[Path] | None = None

    def write_all(self, workers: int = None):
        """Write one page per file; ``workers`` as in :func:`folder_to_md.scan`."""
        emit = [
            p for p in self.index.files
            if self.pages is None or p in self.pages
        ]
        workers = workers or os.cpu_count() or 1
        if workers == 1 or len(emit) < 2:
            for path in tqdm.tqdm(emit, desc="writing"):
                self.write_page(path)
            return
        # workers get a slim index without file contents (the cross-reference
        # maps are all a page needs about *other* files); each page's own
        # FileInfo is shipped with its task. Keeps per-worker memory flat.
        slim = replace(self.index, files={})
        tasks = [(p, self.index.files[p]) for p in emit]
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_write_worker,
            initargs=(slim, self.output_dir, self.pages),
        ) as pool:
            list(
                tqdm.tqdm(
                    pool.map(_write_one, tasks, chunksize=16),
                    total=len(tasks),
                    desc="writing",
                )
            )

    def write_page(self, path: Path, info: FileInfo = None):
        info = info if info is not None else self.index.files[path]
        out_file = self.output_dir / (str(path) + ".md")
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text("".join(self._page(info, out_file)), encoding="utf-8")

    def _link(
        self, target: Path, out_file: Path, anchor_name: str = None, label: str = None
    ) -> str:
        if self.pages is not None and target not in self.pages:
            # indexed for lookup but outside the emitted subgraph: no page
            # exists, so reference it as plain text instead of a dead link
            return f"`{label or target.as_posix()}`"
        target_md = self.output_dir / (str(target) + ".md")
        rel = os.path.relpath(target_md, out_file.parent).replace(os.sep, "/")
        frag = f"#{md_anchor(anchor_name)}" if anchor_name else ""
        return f"[{label or target.as_posix()}]({rel}{frag})"

    def _links(
        self, pairs: list[tuple[str | None, Path]], out_file: Path, exclude: Path = None
    ) -> str | None:
        """Render (anchor name, path) pairs as markdown links, deduped by path."""
        links, seen = [], set()
        for anchor_name, target in pairs:
            if target == exclude or target in seen:
                continue
            seen.add(target)
            links.append(self._link(target, out_file, anchor_name))
        return ", ".join(links) if links else None

    def _page(self, info: FileInfo, out_file: Path) -> list[str]:
        index, path = self.index, info.path
        out = [f"# {path.as_posix()}\n"]

        def links_for(pairs, exclude=path):
            return self._links(pairs, out_file, exclude)

        def site_entries(paths, anchor_name=None):
            # one bullet per file; the page's own file is marked, not linked
            entries = []
            for p in dict.fromkeys(paths):
                if p == path:
                    entries.append(f"- {p.as_posix()} *(this file)*\n")
                else:
                    entries.append(f"- {self._link(p, out_file, anchor_name)}\n")
            return entries

        def caller_entries(pairs):
            entries, seen = [], set()
            for caller, p in pairs:
                if (caller, p) in seen:
                    continue
                seen.add((caller, p))
                entries.append(f"- `{caller}` — {self._link(p, out_file, caller)}\n")
            return entries

        def ref_section(title, entries):
            out.append(f"\n#### {title}\n\n")
            out.extend(entries if entries else ["*None found.*\n"])

        def write_def_uses(calls: Counter, type_uses: list[str]):
            # link each call/class use inside this definition to where it is defined
            lines = []
            for cname, count in calls.items():
                defined = links_for(index.resolve_fn_defs(cname), exclude=None)
                if defined:
                    times = f" (×{count})" if count > 1 else ""
                    lines.append(f"- `{cname}`{times} — {defined}\n")
            if lines:
                out.append("\nCalls:\n\n")
                out.extend(lines)
            lines = []
            for tname in type_uses:
                defined = links_for(
                    [(tname, p) for p in index.class_def_map.get(tname, [])],
                    exclude=None,
                )
                if defined:
                    lines.append(f"- `{tname}` — {defined}\n")
            if lines:
                out.append("\nUses classes:\n\n")
                out.extend(lines)

        if info.includes:
            out.append("\n## Includes\n\n")
            for h in info.includes:
                target = index.resolve_include(h, path)
                if target is not None:
                    out.append(f"- {self._link(target, out_file, label=h)}\n")
                else:
                    out.append(f"- `{h}`\n")

        includers = list(dict.fromkeys(index.included_by.get(path, [])))
        if includers:
            out.append("\n## Included by\n\n")
            for p in sorted(includers):
                out.append(f"- {self._link(p, out_file)}\n")

        if info.class_defs:
            out.append("\n## Class definitions\n")
            for d in info.class_defs:
                out.append(
                    f"\n### `{d.name}`\n\n```{info.language}\n{d.snippet}\n```\n"
                )
                ref_section("Definition sites",
                            site_entries(index.class_def_map.get(d.name, []), d.name))
                ref_section("Declaration sites",
                            site_entries(index.class_decl_map.get(d.name, [])))
                ref_section("Call sites", caller_entries(index.use_sites(d.name)))
                hierarchy = class_hierarchy_diagram(
                    d.name, d.bases, index.derived_classes(d.name))
                if hierarchy:
                    out.append("\n#### Class hierarchy\n")
                    out.extend(hierarchy)
                write_def_uses(d.calls, d.type_uses)

        if info.class_decls:
            out.append("\n## Class declarations\n\n")
            for d in info.class_decls:
                defined = links_for(
                    [(d.name, p) for p in index.class_def_map.get(d.name, [])]
                )
                out.append(
                    f"- `{d.name}`"
                    + (f" — defined in: {defined}\n" if defined else "\n")
                )

        if info.fn_defs:
            out.append("\n## Function definitions\n")
            for d in info.fn_defs:
                out.append(
                    f"\n### `{d.name}`\n\n```{info.language}\n{d.snippet}\n```\n"
                )
                cls = qualifier(d.name)
                if cls:
                    # link a member definition (Class::method) back to its class
                    class_link = links_for(
                        [(cls, p) for p in index.class_def_map.get(cls, [])]
                    )
                    if class_link:
                        out.append(f"\nClass `{cls}`: {class_link}\n")
                ref_section("Definition sites",
                            site_entries(index.fn_def_map.get(d.name, []), d.name))
                ref_section("Declaration sites",
                            site_entries(index.resolve_fn_decls(d.name)))
                ref_section("Call sites", caller_entries(index.call_sites(d.name)))
                callers = list(dict.fromkeys(
                    caller for caller, _ in index.call_sites(d.name)))
                graph = call_graph_diagram(d.name, callers, list(d.calls))
                if graph:
                    out.append("\n#### Call graph\n")
                    out.extend(graph)
                write_def_uses(d.calls, d.type_uses)

        if info.fn_decls:
            out.append("\n## Function declarations\n\n")
            for d in info.fn_decls:
                sig = d.snippet.splitlines()[0]
                defined = links_for(index.resolve_fn_defs(d.name))
                out.append(
                    f"- `{sig}`" + (f" — defined in: {defined}\n" if defined else "\n")
                )

        if info.calls:
            lines, unresolved = [], 0
            for name, count in info.calls.most_common():
                defined = links_for(index.resolve_fn_defs(name), exclude=None)
                declared = links_for(
                    [(None, p) for p in index.resolve_fn_decls(name)], exclude=None
                )
                if not defined and not declared:
                    unresolved += 1
                    continue
                parts = [f"defined in: {defined}"] if defined else []
                if declared:
                    parts.append(f"declared in: {declared}")
                times = f" (×{count})" if count > 1 else ""
                lines.append(f"- `{name}`{times} — {'; '.join(parts)}\n")
            if lines or unresolved:
                out.append("\n## Calls\n\n")
                out.extend(lines)
                if unresolved:
                    out.append(
                        f"\nPlus {unresolved} calls to external/unresolved functions.\n"
                    )
        return out
