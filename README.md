C Linter
========

[![License](https://img.shields.io/badge/license-Apache--2.0%20OR%20MIT-blue.svg)](https://opensource.org/licenses/Apache-2.0)
![CI](https://github.com/SamuelMarks/c-linter/actions/workflows/ci.yml/badge.svg)
![Coverage](https://img.shields.io/badge/Coverage-100%25-brightgreen.svg)
![Doc Coverage](https://img.shields.io/badge/Doc_Coverage-100%25-brightgreen.svg)
![Typing](https://img.shields.io/badge/Typing-Strict-blue.svg)

`c-linter` is a specialized command-line tool and Python SDK designed to strictly enforce rigid C coding standards using the `libclang` AST (Abstract Syntax Tree).

Rather than relying on fragile regex matching, this tool actually parses the code to guarantee 100% accurate structural enforcement. It is ideal for usage in strict CI environments or as a pre-commit hook.

## Enforced Standards

1.  **Strict C89 Compliance:**
    *   The linter enforces `-std=c89 -pedantic`. Features like mixing declarations and code, `//` comments, or `for (int i...)` will immediately fail the lint process.
2.  **Return Type strictness:**
    *   Functions cannot return arbitrary `structs` or `pointers`.
    *   All user-defined function returns must evaluate to `int`, an `enum`, `void`, or a fundamental math type (`float`, `double`). This ensures that complex state passes via pointer arguments and failure states map cleanly to integral statuses.
3.  **Mandatory Allocation Checking:**
    *   The AST is searched for allocations (`malloc`, `calloc`, `realloc`). Any resulting pointer must be explicitly checked against `NULL` (or `!p`) within the same lexical scope before it is used or returned.
4.  **Nodiscard Enforcement:**
    *   Any call to a function that evaluates to an `int` must have its return value evaluated or assigned. It cannot be used in a discarded expression statement, and it cannot be cast to `(void)`.
5.  **Safe CRT Enforcement:**
    *   Usage of unsafe standard C library functions (like `fopen`, `strcpy`, `sprintf`) are banned. The linter suggests the `_s` alternative (e.g. `fopen_s`).
    *   **Exemption:** You can safely fall back to the unsafe function if it is guarded behind an `#else` block mapping to `#ifdef __STDC_WANT_LIB_EXT1__` or standard Windows macros.
6.  **Windows Format Literals:**
    *   Using size and pointer format specifiers like `%zu`, `%I64d`, or `%Iu` are flagged unless the line is strictly wrapped in an `#ifdef` block for Windows (e.g., `WIN32`, `_MSC_VER`, `__CYGWIN__`, `__MINGW64__`).

## Installation

This project is built using Hatch. It depends on `libclang` natively, which is automatically bundled when installing the Python package via PyPI, ensuring a seamless cross-platform experience.

```bash
# Install the linter
pip install c-linter
```

*(Note: For local development, use `pip install -e .` from the repository root).*

## Integration

You can natively enforce these rules across your organization by pulling `c-linter` directly into your CI pipelines. No manual system dependencies are required.

### Pre-commit Hook

To use this linter as a pre-commit hook, add the following to your `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/SamuelMarks/c-linter
    rev: v0.1.0 # Or use a specific commit hash
    hooks:
      - id: c-linter
        # Optional: override default flags
        # args: ["--no-windows", "--no-safe-crt"]
```

### GitHub Action

You can easily consume this repository as a native GitHub Action in your workflows. It will automatically scan all `.c` files in your repository by default.

Create `.github/workflows/lint-c.yml`:

```yaml
name: Lint C Code

on: [push, pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Run C Linter
        uses: SamuelMarks/c-linter@v0.1.0
        with:
          # Optional configurations (these are the defaults):
          # files: '.'
          # no-windows: 'false'
          # no-safe-crt: 'false'
```

You can run the linter directly against one or more files:

```bash
c-linter src/main.c src/utils.c
```

### CLI Flags

*   `--no-windows`: Disables the Windows format literal guard checks.
*   `--no-safe-crt`: Disables Safe CRT function replacement checks.

## Python SDK

The project provides a fully documented, strictly typed Python SDK so you can integrate the AST rules directly into other Python tooling or test suites.

```python
from c_linter import lint_code, lint_file, Issue

# Linting from a string in memory
code = """
int do_something(void) { return 1; }
int main(void) {
    do_something(); /* Error: discarded int */
    return 0;
}
"""

issues = lint_code(code)
for issue in issues:
    print(f"[{issue.line}:{issue.column}] {issue.message}")

# Linting directly from disk (supports the same flags as the CLI)
file_issues = lint_file("src/main.c", check_windows=True, check_safe_crt=False)
```

## Pre-commit Integration

To use this linter as a pre-commit hook, add the following to your `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/SamuelMarks/c-linter
    rev: v0.1.0 # Or use a specific commit hash
    hooks:
      - id: c-linter
```

## Development

The project maintains **100% Test Coverage**, **100% Documentation Coverage**, and **100% Strict Type Annotations**.

To run the development suite:

```bash
# Run tests and assert 100% coverage
pytest --cov=c_linter --cov-report=term-missing

# Run strict type checking
mypy src --strict

# Run docstring coverage
interrogate -v src
```

---

## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE) or <https://www.apache.org/licenses/LICENSE-2.0>)
- MIT license ([LICENSE-MIT](LICENSE-MIT) or <https://opensource.org/licenses/MIT>)

at your option.

### Contribution

Unless you explicitly state otherwise, any contribution intentionally submitted
for inclusion in the work by you, as defined in the Apache-2.0 license, shall be
dual licensed as above, without any additional terms or conditions.
