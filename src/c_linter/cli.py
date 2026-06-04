"""Command-line interface for the C Linter."""

import argparse
import sys
import os

from .linter import lint_file


def main() -> None:
    """The main entry point for the c-linter command-line interface.

    Parses command-line arguments (expecting a list of C files), runs the linter
    on each file, and prints out any resulting diagnostics. It exits with code 1
    if any issues are found, otherwise exits with code 0.
    """
    parser = argparse.ArgumentParser(
        description="C Linter - Enforce strict C codebase standards."
    )
    parser.add_argument("files", nargs="*", help="C source files to lint")
    parser.add_argument(
        "--no-windows",
        action="store_true",
        help="Disable Windows format literal #ifdef checks",
    )
    parser.add_argument(
        "--no-safe-crt",
        action="store_true",
        help="Disable Safe CRT enforcement (e.g., fopen_s)",
    )
    parser.add_argument(
        "-I",
        "--include",
        action="append",
        default=[],
        help="Add directory to include search path",
    )
    parser.add_argument(
        "--ignore-returns",
        type=str,
        default="",
        help="Comma-separated list of functions or macros to ignore discarded returns for",
    )
    parser.add_argument(
        "--std",
        type=str,
        default="c89",
        help="C standard version (e.g., c89, c99, c11)",
    )
    args = parser.parse_args()

    if not args.files:
        print("No files provided for linting.")
        sys.exit(0)

    print(f"Linting {len(args.files)} file(s)...")

    ignore_returns_list = [x.strip() for x in args.ignore_returns.split(",") if x.strip()]

    total_errors = 0
    for f in args.files:
        if not os.path.isfile(f):
            print(f"Error: File '{f}' not found.")
            continue

        issues = lint_file(
            f, 
            check_windows=not args.no_windows, 
            check_safe_crt=not args.no_safe_crt,
            includes=args.include,
            ignore_returns=ignore_returns_list,
            std=args.std,
        )
        if issues:
            print(f"\nIssues in {f}:")
            for issue in issues:
                print(f"  {issue}")
            total_errors += len(issues)

    if total_errors > 0:
        print(f"\nLinting failed with {total_errors} issue(s).")
        sys.exit(1)
    else:
        print("\nLinting passed successfully.")
        sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    main()
