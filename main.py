import argparse
from pathlib import Path

from folder_to_md import generate
from folder_to_md.cpp import CppParser

DEFAULT_REPO = Path("D:/source/llvm-project")
DEFAULT_OUTPUT = Path(__file__).parent / "output"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Turn a folder of source code into cross-linked markdown "
        "pages with full snippets for every class/function definition."
    )
    ap.add_argument(
        "repo", nargs="?", type=Path, default=DEFAULT_REPO,
        help=f"repository root to scan (default: {DEFAULT_REPO})",
    )
    ap.add_argument(
        "-o", "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"directory to write markdown into (default: {DEFAULT_OUTPUT})",
    )
    ap.add_argument(
        "-s", "--subdir", action="append", default=None,
        help="write markdown pages only for this subdirectory (repeatable, "
        "e.g. -s mlir -s llvm). The whole repo is still parsed so pages can "
        "reference the rest of the codebase; see --no-repo-index. "
        "Default: pages for the whole repo",
    )
    ap.add_argument(
        "--no-repo-index", action="store_true",
        help="parse only the -s subdirs instead of the whole repo; "
        "references outside them go unresolved (old behavior)",
    )
    ap.add_argument(
        "-j", "--jobs", type=int, default=None,
        help="worker processes for parsing/writing (default: CPU count; "
        "1 disables parallelism)",
    )
    ap.add_argument(
        "-x", "--exclude", action="append", default=[],
        help="directory name to skip anywhere in the tree (repeatable), "
        "e.g. -x build -x test",
    )
    args = ap.parse_args()

    index = generate(
        args.repo, args.output, parsers=[CppParser()], subdirs=args.subdir,
        workers=args.jobs, excludes=args.exclude,
        index_repo=not args.no_repo_index,
    )
    infos = index.files.values()
    print(
        f"Parsed {len(index.files)} files, "
        f"{sum(len(i.includes) for i in infos)} includes, "
        f"{sum(len(i.class_defs) for i in infos)} class defs, "
        f"{sum(len(i.class_decls) for i in infos)} class decls, "
        f"{sum(len(i.fn_defs) for i in infos)} fn defs, "
        f"{sum(len(i.fn_decls) for i in infos)} fn decls, "
        f"{sum(len(i.calls) for i in infos)} distinct calls"
    )


if __name__ == "__main__":
    main()
