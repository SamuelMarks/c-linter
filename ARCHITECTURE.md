# Architecture

This document describes the high-level architecture of `c-linter`.

`c-linter` is built as a Python CLI and SDK that enforces C coding standards by leveraging `libclang` to parse C source files into an Abstract Syntax Tree (AST). By analyzing the actual semantic structure of the code, it avoids the fragility and false positives common with regex-based linters.

## High-Level Components

The system is composed of three primary modules:

1.  **CLI & Configuration Manager (`cli.py`)**
2.  **Core Linting Engine (`linter.py`)**
3.  **Data Models (`models.py`)**

---

### 1. CLI & Configuration Manager (`src/c_linter/cli.py`)

This component serves as the entry point for end-users executing `c-linter` from the terminal.

*   **Argument Parsing:** Uses `argparse` to handle command-line flags, input files, and directory scanning.
*   **Configuration Loading:** Merges configuration from `pyproject.toml` or `.c-linter.toml` with the command-line arguments. CLI flags take precedence over file-based configuration.
*   **Auto-discovery:**
    *   Automatically locates `compile_commands.json` in `build/` or `out/` directories to extract precise compiler flags, include paths, and definitions used by the project's build system.
    *   Automatically detects and includes standard local include directories (e.g., `include/`, `src/`) if they exist.
*   **File Gathering & Filtering:** Walks through provided directories, finding `.c` and `.h` files while honoring exclusion patterns (e.g., `build/**`, `vendor/**`).
*   **Execution:** Loops through discovered files, invokes the core engine, and formats the final output, returning a non-zero exit code if issues are found.

### 2. Core Linting Engine (`src/c_linter/linter.py`)

This is the core of the application where the semantic analysis occurs. It utilizes the official Python bindings for `libclang` (`clang.cindex`).

The linting process is divided into several phases:

#### Phase A: Clang Invocation & Diagnostics
The engine instantiates a `clang` `Index` and parses the file into a `TranslationUnit` (TU). It extracts compiler diagnostics to catch standard violations:
*   Enforces C89 compliance by passing `-std=c89 -pedantic`.
*   Catches mixing declarations and code, `//` comments, and missing newlines at the end of files.
*   Optionally injects standard types for standalone header linting to prevent missing type errors.

#### Phase B: AST Traversal (`_analyze_ast`)
A recursive function walks the generated AST (Abstract Syntax Tree) node by node:
*   **Function Declarations (`CursorKind.FUNCTION_DECL`):** Inspects the return type of user-defined functions to ensure they return standard types (`int`, `enum`, `void`, `float`, etc.) and explicitly reject arbitrary structs or pointers.
*   **Call Expressions (`CursorKind.CALL_EXPR`):**
    *   **Nodiscard Check:** Checks if the function returns an `int`. If so, it verifies that the parent AST node is not a compound statement (meaning the return value is being discarded without assignment or evaluation).
    *   **Safe CRT Check:** Checks if the called function is in the `UNSAFE_CRT_FUNCS` set (e.g., `fopen`, `strcpy`). It cross-references the line number against preprocessor macro guards to allow exceptions if guarded by specific macros like `__STDC_WANT_LIB_EXT1__`.

#### Phase C: Allocation Validation (`_check_allocations`)
A specialized AST pass dedicated to memory safety:
1.  **Tracking:** Finds variable declarations or assignments resulting from `malloc`, `calloc`, or `realloc`.
2.  **Validation:** Walks down the AST to find relational operators (`==`, `!=`), `if` statements, or pointer dereferences involving the allocated variable.
3.  **Reporting:** If a pointer is dereferenced or returned *before* an AST node confirms it was checked against `NULL`, an issue is recorded.

#### Phase D: Textual/Regex Analysis
While primarily AST-driven, some tasks require direct source text analysis:
*   **Windows Format Literals:** Uses regex to find format specifiers like `%I64d` within strings, cross-referencing against lines inside `#ifdef WIN32` style macro blocks.
*   **Inline Suppressions:** Parses the file for `// NOLINT` or `// c-linter-disable-file` directives, allowing users to suppress specific lines or scopes. Issues generated on these lines are filtered out before returning.

### 3. Data Models (`src/c_linter/models.py`)

A minimalistic module containing standard dataclasses.

*   **`Issue`:** Represents a single linting violation. It contains the file path, line number, column number, the descriptive error message, and a flag indicating if the issue was automatically fixed. It defines a standard compiler-like `__str__` format (`file:line:column: message`) for easy integration with IDEs and CI parsers.

## Dependency Management

The only heavy runtime dependency is `libclang`. `c-linter` avoids relying on a system-installed `clang` binary by depending on the `libclang` package from PyPI, which bundles the native shared libraries across platforms (Windows, macOS, Linux). This ensures deterministic and seamless installation in CI environments.
