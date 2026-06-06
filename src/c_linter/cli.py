"""Command-line interface for the C Linter."""

import argparse
import sys
import os
from pathlib import Path
from typing import List, Dict, Any

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from .linter import lint_file


def _load_config() -> Dict[str, Any]:
    """Load configuration from .c-linter.toml or pyproject.toml."""
    config: Dict[str, Any] = {}

    if os.path.isfile(".c-linter.toml"):
        try:
            with open(".c-linter.toml", "rb") as f:
                config = tomllib.load(f)
        except Exception:
            pass
    elif os.path.isfile("pyproject.toml"):
        try:
            with open("pyproject.toml", "rb") as f:
                pyproject = tomllib.load(f)
                config = pyproject.get("tool", {}).get("c-linter", {})
        except Exception:
            pass

    return config


def _find_compile_commands(search_paths: List[str]) -> str:
    """Auto-discover compile_commands.json."""
    for d in [".", "build", "out"]:
        if os.path.isfile(os.path.join(d, "compile_commands.json")):
            return d

    for p in search_paths:
        base = (
            os.path.abspath(p)
            if os.path.isdir(p)
            else os.path.dirname(os.path.abspath(p))
        )
        while True:
            for d in [base, os.path.join(base, "build"), os.path.join(base, "out")]:
                if os.path.isfile(os.path.join(d, "compile_commands.json")):
                    return d
            parent = os.path.dirname(base)
            if parent == base:
                break
            base = parent

    return ""


def _match_exclude(filepath: str, exclude_patterns: List[str]) -> bool:
    """Check if a file matches any of the exclude patterns."""
    import fnmatch

    path_obj = Path(filepath)
    normalized_path = filepath.replace(os.sep, "/")
    for pattern in exclude_patterns:
        # Strip trailing slashes and ** for part matching
        clean_pattern = pattern.strip("/")
        if clean_pattern.endswith("/**"):
            clean_pattern = clean_pattern[:-3]
        elif clean_pattern.endswith("**"):
            clean_pattern = clean_pattern[:-2]

        if any(fnmatch.fnmatch(part, clean_pattern) for part in path_obj.parts):
            return True  # pragma: no cover

        if fnmatch.fnmatch(normalized_path, pattern):
            return True
        if fnmatch.fnmatch(normalized_path, "*/" + pattern):
            return True  # pragma: no cover
    return False


def _gather_files(paths: List[str], exclude_patterns: List[str]) -> List[str]:
    """Gather .c and .h files from paths or directories."""
    files = set()
    for p in paths:
        if os.path.isfile(p):
            if not _match_exclude(p, exclude_patterns):
                files.add(os.path.abspath(p))
        elif os.path.isdir(p):
            for root, _, filenames in os.walk(p):
                for name in filenames:
                    if name.endswith(".c") or name.endswith(".h"):
                        full_path = os.path.join(root, name)
                        if not _match_exclude(full_path, exclude_patterns):
                            files.add(os.path.abspath(full_path))
    return sorted(list(files))


def _gather_include_dirs(paths: List[str], exclude_patterns: List[str]) -> List[str]:
    """Gather directories containing .h files from the input paths."""
    include_dirs = set()
    for p in paths:
        if os.path.isdir(p):
            for root, _, filenames in os.walk(p):
                if any(name.endswith(".h") for name in filenames):
                    if not _match_exclude(root, exclude_patterns):
                        include_dirs.add(os.path.abspath(root))
        elif os.path.isfile(p) and p.endswith(".h"):
            dir_path = os.path.dirname(os.path.abspath(p))
            if not _match_exclude(dir_path, exclude_patterns):
                include_dirs.add(dir_path)
    return sorted(list(include_dirs))


def main() -> None:
    """Execute the main entry point for the c-linter command-line interface."""
    config = _load_config()

    parser = argparse.ArgumentParser(
        description="C Linter - Enforce strict C codebase standards."
    )

    # Core positional argument
    parser.add_argument(
        "files", nargs="*", help="C source files or directories to lint"
    )

    # 1. Rules & Enforcement
    rules_group = parser.add_argument_group("Rule Configuration")
    rules_group.add_argument(
        "--std",
        type=str,
        default=config.get("std", "c89"),
        help="C standard version (e.g., c89, c99, c11)",
    )
    rules_group.add_argument(
        "--no-windows",
        action="store_true",
        default=config.get("no_windows", False),
        help="Disable Windows format literal #ifdef checks",
    )
    rules_group.add_argument(
        "--no-safe-crt",
        action="store_true",
        default=config.get("no_safe_crt", False),
        help="Disable Safe CRT enforcement (e.g., fopen_s)",
    )
    rules_group.add_argument(
        "--strict-safe-crt",
        action="store_true",
        default=config.get("strict_safe_crt", False),
        help="Enable strict Safe CRT enforcement (flags strncpy and strncat)",
    )
    rules_group.add_argument(
        "--no-discarded-returns",
        action="store_true",
        default=config.get("no_discarded_returns", False),
        help="Disable the discarded return value check globally",
    )
    rules_group.add_argument(
        "--no-tolerate-c99",
        action="store_true",
        default=config.get("no_tolerate_c99", False),
        help="Do not tolerate C99 type extensions (like _Bool or long long) in C89 mode",
    )
    rules_group.add_argument(
        "--no-test-relaxations",
        action="store_true",
        default=config.get("no_test_relaxations", False),
        help="Disable relaxed rules for test files",
    )
    rules_group.add_argument(
        "--freestanding",
        action="store_true",
        default=config.get("freestanding", False),
        help="Enforce a freestanding environment (disables built-in headers)",
    )

    # 2. Build & Environment
    build_group = parser.add_argument_group("Build & Environment")
    build_group.add_argument(
        "-I",
        "--include",
        action="append",
        default=config.get("include", []),
        help="Add directory to include search path",
    )
    build_group.add_argument(
        "-p",
        "--build-dir",
        type=str,
        default=config.get("build_dir", ""),
        help="Path to build directory containing compile_commands.json",
    )
    build_group.add_argument(
        "--no-header-strategy",
        action="store_true",
        default=config.get("no_header_strategy", False),
        help="Disable auto-injection of standard headers when linting standalone .h files",
    )

    # 3. Exclusions & Suppressions
    exc_group = parser.add_argument_group("Exclusions & Suppressions")
    default_excludes = config.get("exclude", []) + [
        "build/**",
        "out/**",
        ".git/**",
        "_deps/**",
        "wasi-sdk-*/**",
        "vendor/**",
        "node_modules/**",
    ]
    exc_group.add_argument(
        "--exclude",
        action="append",
        default=default_excludes,
        help="Glob pattern to exclude files/directories",
    )
    exc_group.add_argument(
        "--safe-crt-exclude",
        action="append",
        default=config.get("safe_crt_exclude", []),
        help="Glob pattern to exclude files/directories from Safe CRT checks",
    )
    exc_group.add_argument(
        "--ignore-returns",
        type=str,
        default=",".join(config.get("ignore_returns", [])),
        help="Comma-separated list of functions or macros to ignore discarded returns for",
    )
    exc_group.add_argument(
        "--ignore-missing-includes",
        action="store_true",
        default=config.get("ignore_missing_includes", False),
        help="Suppress 'file not found' diagnostics",
    )
    exc_group.add_argument(
        "--no-pedantic",
        action="store_true",
        default=config.get("no_pedantic", False),
        help="Suppress standard compiler pedantic warnings like 'no newline at end of file'",
    )
    exc_group.add_argument(
        "--ignore-formatting",
        action="store_true",
        default=config.get("ignore_formatting", False),
        help="Alias for --no-pedantic",
    )

    # 4. Output & Formatting
    out_group = parser.add_argument_group("Output & Actions")
    out_group.add_argument(
        "--max-issues-per-file",
        type=int,
        default=config.get("max_issues_per_file", 50),
        help="Maximum number of compiler diagnostics to report per file (0 to disable limit, default: 50)",
    )
    out_group.add_argument(
        "--fix",
        action="store_true",
        default=config.get("fix", False),
        help="Automatically fix trivial warnings (e.g., missing newlines at EOF)",
    )

    parser.epilog = (
        "Inline Suppression:\n"
        "  // NOLINT                   Suppress all warnings on the current line\n"
        "  // NOLINT(safe-crt)         Suppress specific warning categories on the current line\n"
        "  // NOLINTNEXTLINE           Suppress all warnings on the next line\n"
        "  // c-linter-disable-file    Suppress all warnings for the entire file\n"
        "  // NOLINTFILE               Alias for c-linter-disable-file"
    )
    parser.formatter_class = argparse.RawDescriptionHelpFormatter

    args = parser.parse_args()

    files_to_lint = _gather_files(args.files, args.exclude)

    if not files_to_lint:
        print("No files provided for linting.")
        sys.exit(0)

    print(f"Linting {len(files_to_lint)} file(s)...")

    if args.ignore_returns:
        ignore_returns_list = [
            x.strip() for x in args.ignore_returns.split(",") if x.strip()
        ]
    else:
        ignore_returns_list = []

    if args.no_discarded_returns:
        ignore_returns_list.append("*")

    build_dir = args.build_dir
    if not build_dir:
        build_dir = _find_compile_commands(args.files)

    includes = args.include if args.include else []

    # Auto-detect all include directories from provided paths
    auto_includes = _gather_include_dirs(args.files, args.exclude)
    for ai in auto_includes:
        if ai not in includes:
            includes.append(ai)

    if (
        os.path.isdir("include")
        and os.path.abspath("include") not in includes
        and "include" not in includes
    ):  # pragma: no cover
        includes.append("include")  # pragma: no cover
    if (
        os.path.isdir("src")
        and os.path.abspath("src") not in includes
        and "src" not in includes
    ):  # pragma: no cover
        includes.append("src")  # pragma: no cover

    total_errors = 0
    for f in files_to_lint:
        file_check_safe_crt = not args.no_safe_crt
        if file_check_safe_crt and _match_exclude(f, args.safe_crt_exclude):
            file_check_safe_crt = False

        issues = lint_file(
            f,
            check_windows=not args.no_windows,
            check_safe_crt=file_check_safe_crt,
            strict_safe_crt=args.strict_safe_crt,
            includes=includes,
            ignore_returns=ignore_returns_list,
            std=args.std,
            build_dir=build_dir,
            tolerate_c99_types=not args.no_tolerate_c99,
            header_only_strategy=not args.no_header_strategy,
            ignore_missing_includes=args.ignore_missing_includes,
            no_test_relaxations=args.no_test_relaxations,
            freestanding=args.freestanding,
            max_issues_per_file=args.max_issues_per_file,
            fix_issues=args.fix,
            no_pedantic=args.no_pedantic or args.ignore_formatting,
        )
        if issues:
            print(f"\nIssues in {f}:")
            for issue in issues:
                print(f"  {issue}")  # pragma: no cover
                if not getattr(issue, "fixed", False):
                    total_errors += 1

    if total_errors > 0:
        print(f"\nLinting failed with {total_errors} issue(s).")
        sys.exit(1)
    else:
        print("\nLinting passed successfully.")
        sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    main()
