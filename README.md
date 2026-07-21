# folder_to_md

Turn a folder of source code into a mirror tree of cross-linked markdown pages:
one page per source file, with full code snippets for every class/function
definition and links between definitions, declarations, calls, and includes.

## Usage

```python
from pathlib import Path
from folder_to_md import generate
from folder_to_md.cpp import CppParser

generate(Path("path/to/repo"), Path("output"),
         parsers=[CppParser()], subdir="optional/subdir")
```

Or edit `REPO` in `main.py` and run `uv run python main.py`.

## Architecture

```
scan()  ->  LanguageParser.parse()  ->  RepoIndex.build()  ->  MarkdownWriter.write_all()
(walk files)   (per-file facts)       (cross-reference maps)     (one .md per file)
```

- `folder_to_md/model.py` — the language-agnostic data model: `FileInfo`
  (per-file facts), `Definition` (name, full snippet, calls made, classes
  used), `Declaration` (name, signature).
- `folder_to_md/parser.py` — the `LanguageParser` interface plus qualified-name
  helpers (`tail_name`, `qualifier`).
- `folder_to_md/cpp.py` — `CppParser`, a tree-sitter C/C++ implementation.
- `folder_to_md/index.py` — `RepoIndex`: name → defining/declaring files,
  unqualified-name resolution (`x.size()` → `Widget::size`), include
  resolution by path-tail matching.
- `folder_to_md/markdown.py` — `MarkdownWriter`: renders pages with sections
  for Includes, Class/Function definitions, Class/Function declarations, and
  file-wide Calls. Every definition gets its full snippet plus three
  reference subsections — **Definition sites**, **Declaration sites**, and
  **Call sites** (who calls the function / uses the class) — and a Mermaid
  diagram: a `classDiagram` of bases and derived classes for classes, a
  `flowchart` call graph (callers → function → callees) for functions.

## Adding a language

Subclass `LanguageParser`, set `extensions`, and implement
`parse(rel_path, source) -> FileInfo`:

```python
class PyParser(LanguageParser):
    extensions = frozenset({".py"})

    def parse(self, rel_path, source):
        info = FileInfo(path=rel_path, language="python")
        # fill info.class_defs / fn_defs with Definition(name, snippet, calls, type_uses),
        # info.class_decls / fn_decls with Declaration(name, snippet),
        # info.includes with import path strings, info.calls with a Counter
        return info
```

Conventions the index relies on:

- qualified names use `::` or `.` (e.g. `Widget::size`, `Widget.size`) so
  unqualified call names can resolve to them;
- `includes` are path-like strings resolved against known file paths by tail
  matching;
- `language` is the markdown code-fence tag for snippets.

Then pass an instance in `parsers=[...]` — multiple parsers can run in one
scan, and cross-file links work across all of them.
