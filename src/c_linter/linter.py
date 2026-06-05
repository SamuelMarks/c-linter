"""Core linting logic utilizing libclang for AST analysis."""

import os
import re
from typing import List, Optional, Set, Dict, Tuple, Iterable, Any

# We ignore missing types from clang as it does not ship with a py.typed marker.
from clang.cindex import (
    Index,
    Cursor,
    CursorKind,
    TypeKind,
    TranslationUnit,
    CompilationDatabase,
    CompilationDatabaseError,
)  # type: ignore

from .models import Issue


def _extract_clang_args(args: List[str]) -> List[str]:
    """Extract relevant compiler arguments from a compile command.

    Args:
        args (List[str]): Full list of compiler arguments.

    Returns:
        List[str]: Filtered list of arguments relevant for parsing (includes, defines).
    """
    result: List[str] = []
    if not args:
        return result
    i = 1  # Skip compiler executable
    while i < len(args):
        arg = args[i]
        if arg in ("-I", "-isystem"):
            result.append(arg)
            if i + 1 < len(args):
                result.append(args[i + 1])
                i += 1
        elif (
            arg.startswith("-I")
            or arg.startswith("-isystem")
            or arg.startswith("-D")
            or arg.startswith("-U")
            or arg.startswith("-std=")
            or arg.startswith("-m")
        ):
            result.append(arg)
        i += 1
    return result


def _get_diagnostics(
    tu: TranslationUnit,
    main_filename: str,
    ignore_missing_includes: bool = False,
    max_issues: int = 50,
    fix_issues: bool = False,
    no_pedantic: bool = False,
) -> Tuple[List[Issue], Set[str]]:
    """Extract standard clang diagnostics (including C89 violations).

    Args:
        tu (TranslationUnit): The parsed translation unit.
        main_filename (str): The primary filename being linted.
        ignore_missing_includes (bool): Suppress 'file not found' and cascading type errors.

    Returns:
        Tuple[List[Issue], Set[str]]: A tuple of issues and implicitly declared functions.
    """
    issues: List[Issue] = []
    implicit_funcs: Set[str] = set()
    main_abs = os.path.abspath(main_filename)

    # Patterns to suppress when missing includes are ignored
    cascade_patterns = [
        re.compile(r"^unknown type name .*"),
        re.compile(r"^incomplete definition of type .*"),
        re.compile(r"^use of undeclared identifier .*"),
        re.compile(r"^implicitly declaring library function .*"),
        re.compile(r"^declaration of .* will not be visible outside of this function"),
        re.compile(r"^invalid application of 'sizeof' to an incomplete type .*"),
        re.compile(r"^variable has incomplete type .*"),
        re.compile(r"^conflicting types for .*"),
        re.compile(r"^implicit declaration of function '(.*)'"),
        re.compile(r"^.* file not found$"),
    ]

    for diag in tu.diagnostics:
        filename: str = diag.location.file.name if diag.location.file else "unknown"
        if filename == "unknown" or os.path.abspath(filename) != main_abs:
            continue

        msg = diag.spelling

        if msg == "no newline at end of file":
            if fix_issues:
                try:
                    with open(filename, "a", encoding="utf-8") as f:
                        f.write("\n")
                    continue
                except Exception:
                    pass
            elif no_pedantic:
                continue

        # Track implicit declarations
        implicit_match = re.search(r"implicit declaration of function '(.*)'", msg)
        if implicit_match:
            implicit_funcs.add(implicit_match.group(1))

        if ignore_missing_includes:
            if any(p.match(msg) for p in cascade_patterns):
                continue

        issues.append(
            Issue(
                file=filename,
                line=diag.location.line,
                column=diag.location.column,
                message=msg,
            )
        )

        # Cap errors to prevent unreadable cascades
        if max_issues > 0 and len(issues) >= max_issues:
            issues.append(
                Issue(
                    file=filename,
                    line=0,
                    column=0,
                    message="Too many compiler diagnostics generated. Truncating output to prevent cascading cascades.",
                )
            )
            break

    return issues, implicit_funcs


UNSAFE_CRT_FUNCS: Set[str] = {
    "strcpy",
    "wcscpy",
    "strcat",
    "wcscat",
    "sprintf",
    "swprintf",
    "vsprintf",
    "vswprintf",
    "gets",
    "_getws",
    "fopen",
    "_wfopen",
    "freopen",
    "_wfreopen",
    "strtok",
    "wcstok",
    "strncpy",
    "wcsncpy",
    "strncat",
    "wcsncat",
}
"""Set[str]: A set of standard CRT functions that are considered unsafe and should be replaced with _s alternatives."""


WIN_MACROS: Tuple[str, ...] = (
    "WIN32",
    "_WIN32",
    "__WIN32__",
    "__WIN32",
    "WIN64",
    "_WIN64",
    "__WIN64__",
    "__WIN64",
    "_MSC_VER",
    "__MINGW32__",
    "__MINGW64__",
    "__CYGWIN__",
    "__WINDOWS__",
)
"""Tuple[str, ...]: A tuple of standard preprocessor macros used to identify Windows-specific compilation blocks."""

SAFE_CRT_MACROS: Tuple[str, ...] = WIN_MACROS + ("__STDC_WANT_LIB_EXT1__",)
"""Tuple[str, ...]: A tuple of preprocessor macros that permit the fallback usage of non-secure CRT functions."""


def _get_guarded_lines(source_code: str, macros: Tuple[str, ...]) -> Set[int]:
    """Identify lines of code within specific preprocessor guards.

    Args:
        source_code (str): The C source code to analyze.
        macros (Tuple[str, ...]): A tuple of macro names to check for in #if/#ifdef blocks.

    Returns:
        Set[int]: A set of line numbers (1-indexed) that are guarded.
    """
    guarded_lines: Set[int] = set()
    guard_depth: int = 0
    active_depths: Set[int] = set()

    for i, line in enumerate(source_code.splitlines(), 1):
        stripped: str = line.strip()
        if stripped.startswith("#if"):
            guard_depth += 1
            if any(macro in stripped for macro in macros):
                active_depths.add(guard_depth)
        elif stripped.startswith("#endif"):
            if guard_depth in active_depths:
                active_depths.remove(guard_depth)
            guard_depth -= 1

        if active_depths:
            guarded_lines.add(i)
    return guarded_lines


def _check_windows_format_literals(
    source_code: str, filename: str, issues: List[Issue]
) -> None:
    """Check for Windows-specific format literals missing #ifdef guards.

    Args:
        source_code (str): The C source code to analyze.
        filename (str): The name of the file being linted.
        issues (List[Issue]): The list to append any found issues to.
    """
    guarded_lines: Set[int] = _get_guarded_lines(source_code, WIN_MACROS)
    string_literal_re: re.Pattern[str] = re.compile(r'"([^"\\]*(\\.[^"\\]*)*)"')
    format_specifiers_re: re.Pattern[str] = re.compile(r"%(?:I64[udixX]|I[udixX])")

    for i, line in enumerate(source_code.splitlines(), 1):
        for match in string_literal_re.finditer(line):
            literal: str = match.group(1)
            if format_specifiers_re.search(literal):
                if i not in guarded_lines:
                    issues.append(
                        Issue(
                            file=filename,
                            line=i,
                            column=match.start() + 1,
                            message="Format specifier (I64/I) used without Windows #ifdef guard.",
                        )
                    )


DEFAULT_IGNORE_RETURNS: Set[str] = {
    "printf",
    "fprintf",
    "sprintf",
    "snprintf",
    "fflush",
    "remove",
    "Sleep",
    "vfprintf",
    "vsnprintf",
    "vsprintf",
    "puts",
    "fputs",
    "fputc",
    "putc",
    "putchar",
    "vsnprintf_s",
    "vsprintf_s",
    "jasprintf",
    "vasprintf",
    "asprintf",
    "fopen_s",
    "fclose",
    "fseek",
    "sprintf_s",
    "strcpy_s",
    "strncpy_s",
    "strcat_s",
    "memcpy_s",
    "memmove_s",
    "_putenv_s",
    "_putenv",
    "system",
    "WSAStartup",
    "WSACleanup",
    "CloseHandle",
    "SetEvent",
    "ResetEvent",
    "closesocket",
    "shutdown",
    "InternetCloseHandle",
    "WinHttpCloseHandle",
    "WinHttpSetOption",
    "send",
    "recv",
    "cdd_mutex_unlock",
}
"""Set[str]: A set of standard functions whose returns are conventionally ignored."""


def _analyze_ast(
    cursor: Cursor,
    filename: str,
    issues: List[Issue],
    check_safe_crt: bool,
    strict_safe_crt: bool,
    safe_crt_guarded_lines: Set[int],
    ignore_returns: List[str],
    source_code: str = "",
    parent: Optional[Cursor] = None,
) -> None:
    """Recursively analyze the AST for return type, nodiscard, and Safe CRT violations.

    Args:
        cursor (Cursor): The current AST node being analyzed.
        filename (str): The primary filename being linted (to ignore headers).
        issues (List[Issue]): The list to append found issues to.
        check_safe_crt (bool): Whether to enforce safe CRT functions.
        strict_safe_crt (bool): Whether to enforce strict safe CRT functions.
        safe_crt_guarded_lines (Set[int]): Lines that are exempted from Safe CRT checks.
        ignore_returns (List[str]): List of macros/functions to ignore discarded returns for.
        source_code (str): Full source code of the file.
        parent (Optional[Cursor]): The parent of the current AST node.
    """
    import fnmatch

    if cursor.location.file and os.path.abspath(
        cursor.location.file.name
    ) != os.path.abspath(filename):
        return

    # Check 1: Return types of user-defined functions
    if cursor.kind == CursorKind.FUNCTION_DECL and cursor.is_definition():
        try:
            ret_type = cursor.result_type.kind
        except ValueError:  # pragma: no cover
            ret_type = None

        if ret_type is not None:
            # Allow void, ints, enums, math types, pointers, records, bools, uchars, long longs
            allowed_types: Set[Any] = {
                TypeKind.VOID,
                TypeKind.BOOL,
                TypeKind.CHAR_U,
                TypeKind.UCHAR,
                TypeKind.CHAR16,
                TypeKind.CHAR32,
                TypeKind.USHORT,
                TypeKind.UINT,
                TypeKind.ULONG,
                TypeKind.ULONGLONG,
                TypeKind.UINT128,
                TypeKind.CHAR_S,
                TypeKind.SCHAR,
                TypeKind.WCHAR,
                TypeKind.SHORT,
                TypeKind.INT,
                TypeKind.LONG,
                TypeKind.LONGLONG,
                TypeKind.INT128,
                TypeKind.ENUM,
                TypeKind.FLOAT,
                TypeKind.DOUBLE,
                TypeKind.LONGDOUBLE,
                TypeKind.POINTER,
                TypeKind.RECORD,
            }
            if ret_type not in allowed_types:
                try:
                    canonical_kind = cursor.result_type.get_canonical().kind
                except ValueError:  # pragma: no cover
                    canonical_kind = None

                if canonical_kind not in allowed_types:
                    msg: str = (
                        f"Function '{cursor.spelling}' returns non-compliant type "
                        f"({canonical_kind.name if canonical_kind else 'UNKNOWN'})."
                    )
                    issues.append(
                        Issue(
                            file=filename,
                            line=cursor.location.line,
                            column=cursor.location.column,
                            message=msg,
                        )
                    )

    if cursor.kind == CursorKind.CALL_EXPR:
        try:
            call_type_kind = cursor.type.get_canonical().kind
        except ValueError:  # pragma: no cover
            call_type_kind = None

        # Check 2: Nodiscard focus on int functions
        if call_type_kind == TypeKind.INT:
            is_ignored = False

            # Check implicit declaration
            if (
                cursor.referenced
                and cursor.referenced.location.line == cursor.location.line
                and cursor.referenced.location.column == cursor.location.column
            ):
                is_ignored = True

            spelling = cursor.spelling

            def check_ignored(name: str) -> bool:
                """Check if a function name matches ignored patterns."""
                if any(fnmatch.fnmatch(name, pattern) for pattern in ignore_returns):
                    return True
                if any(
                    fnmatch.fnmatch(name, pattern) for pattern in DEFAULT_IGNORE_RETURNS
                ):
                    return True
                prefixes = ("ASSERT_", "EXPECT_", "TEST_", "LOG_", "PASS")
                if any(name.startswith(p) for p in prefixes):
                    return True
                suffixes = ("_free", "_destroy", "_cleanup", "_close")
                if any(name.endswith(s) for s in suffixes):
                    return True
                if (
                    "ASSERT" in name
                    or "EXPECT" in name
                    or "FREE" in name
                    or "LOG" in name
                ):
                    return True  # pragma: no cover
                return False

            if check_ignored(spelling):
                is_ignored = True

            # Check source code token at the location
            if not is_ignored and source_code and cursor.extent.start.line > 0:
                try:
                    lines = source_code.splitlines()
                    line_idx = cursor.extent.start.line - 1
                    if line_idx < len(lines):
                        line_str = lines[line_idx]
                        col_idx = cursor.extent.start.column - 1
                        if col_idx < len(line_str):
                            substring = line_str[col_idx:]
                            match = re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*", substring)
                            if match and check_ignored(match.group(0)):
                                is_ignored = True
                except Exception:
                    pass

            if not is_ignored:
                if parent and parent.kind in (
                    CursorKind.COMPOUND_STMT,
                    CursorKind.FOR_STMT,
                ):
                    msg = f"Call to int-returning function '{cursor.spelling}' is discarded. Must be assigned."
                    issues.append(
                        Issue(
                            file=filename,
                            line=cursor.location.line,
                            column=cursor.location.column,
                            message=msg,
                        )
                    )

        # Check 4: Safe CRT
        if check_safe_crt and cursor.spelling in UNSAFE_CRT_FUNCS:
            is_strict_func = cursor.spelling in (
                "strncpy",
                "strncat",
                "wcsncpy",
                "wcsncat",
            )
            if not is_strict_func or strict_safe_crt:
                if cursor.location.line not in safe_crt_guarded_lines:
                    msg = f"Use safe CRT alternative for '{cursor.spelling}' (e.g., '{cursor.spelling}_s')."
                    issues.append(
                        Issue(
                            file=filename,
                            line=cursor.location.line,
                            column=cursor.location.column,
                            message=msg,
                        )
                    )

    for child in cursor.get_children():
        _analyze_ast(
            child,
            filename,
            issues,
            check_safe_crt,
            strict_safe_crt,
            safe_crt_guarded_lines,
            ignore_returns,
            source_code=source_code,
            parent=cursor,
        )


def _check_allocations(tu_cursor: Cursor, filename: str, issues: List[Issue]) -> None:
    """Analyze the AST for un-checked memory allocations.

    Args:
        tu_cursor (Cursor): The root AST node of the translation unit.
        filename (str): The primary filename being linted.
        issues (List[Issue]): The list to append found issues to.
    """

    def find_function_decls(cursor: Cursor) -> Iterable[Cursor]:
        """Yield all function declarations in the current file.

        Args:
            cursor (Cursor): The AST node to search.

        Yields:
            Cursor: Function declaration cursors.
        """
        if cursor.location.file and os.path.abspath(
            cursor.location.file.name
        ) == os.path.abspath(filename):
            if cursor.kind == CursorKind.FUNCTION_DECL and cursor.is_definition():
                yield cursor
        for child in cursor.get_children():
            yield from find_function_decls(child)

    def walk_allocs_and_checks(
        cursor: Cursor,
        parent: Optional[Cursor],
        allocated_vars: Dict[str, Tuple[int, int]],
        checked_vars: Set[str],
    ) -> None:
        """Walk the AST to find memory allocations and their subsequent NULL checks.

        Args:
            cursor (Cursor): The current AST node.
            parent (Optional[Cursor]): The parent of the current AST node.
            allocated_vars (Dict[str, Tuple[int, int]]): Variables that have received allocated memory.
            checked_vars (Set[str]): Variables that have been validated against NULL.
        """
        if cursor.kind == CursorKind.CALL_EXPR and cursor.spelling in (
            "malloc",
            "calloc",
            "realloc",
        ):
            if parent and parent.kind == CursorKind.VAR_DECL:
                allocated_vars[parent.spelling] = (
                    cursor.location.line,
                    cursor.location.column,
                )
            elif parent and parent.kind == CursorKind.BINARY_OPERATOR:
                children = list(parent.get_children())
                if len(children) >= 1 and children[0].kind == CursorKind.DECL_REF_EXPR:
                    allocated_vars[children[0].spelling] = (
                        cursor.location.line,
                        cursor.location.column,
                    )

        def find_decl_refs(c: Cursor, is_deref: bool = False) -> None:
            """Recursively find and record declaration references.

            Args:
                c (Cursor): The AST node to check.
                is_deref (bool): Whether the context is a dereference.
            """
            if c.kind == CursorKind.DECL_REF_EXPR:
                if is_deref:
                    if c.spelling in allocated_vars and c.spelling not in checked_vars:
                        # Flag premature dereference
                        issues.append(
                            Issue(
                                file=filename,
                                line=c.location.line,
                                column=c.location.column,
                                message=f"Potential failure from allocation assigned to '{c.spelling}' is not checked.",
                            )
                        )
                else:
                    checked_vars.add(c.spelling)
            for child in c.get_children():
                find_decl_refs(child, is_deref)

        if cursor.kind == CursorKind.UNARY_OPERATOR:
            tokens = list(cursor.get_tokens())
            if tokens and tokens[0].spelling == "!":
                for child in cursor.get_children():
                    find_decl_refs(child)
            elif tokens and tokens[0].spelling == "*":
                for child in cursor.get_children():
                    find_decl_refs(child, is_deref=True)

        if cursor.kind in (CursorKind.ARRAY_SUBSCRIPT_EXPR, CursorKind.MEMBER_REF_EXPR):
            for child in cursor.get_children():
                find_decl_refs(child, is_deref=True)

        # Relational operators in C return INT (e.g., ==, !=, <, >)
        if (
            cursor.kind == CursorKind.BINARY_OPERATOR
            and cursor.type.get_canonical().kind == TypeKind.INT
        ):
            tokens = list(cursor.get_tokens())
            if any(t.spelling in ("==", "!=", "<", "<=", ">", ">=") for t in tokens):
                for child in cursor.get_children():
                    find_decl_refs(child)

        # Direct condition checks (if (p))
        if cursor.kind in (
            CursorKind.IF_STMT,
            CursorKind.WHILE_STMT,
            CursorKind.CONDITIONAL_OPERATOR,
        ):
            children = list(cursor.get_children())
            if children:
                find_decl_refs(children[0])

        # Returning a pointer transfers ownership/check responsibility
        if cursor.kind == CursorKind.RETURN_STMT:
            for child in cursor.get_children():
                find_decl_refs(child)

        for child in cursor.get_children():
            next_parent = cursor if cursor.kind != CursorKind.UNEXPOSED_EXPR else parent
            walk_allocs_and_checks(child, next_parent, allocated_vars, checked_vars)

    for func in find_function_decls(tu_cursor):
        allocated_vars: Dict[str, Tuple[int, int]] = {}
        checked_vars: Set[str] = set()
        walk_allocs_and_checks(func, None, allocated_vars, checked_vars)
        print(f"Allocated: {allocated_vars}, Checked: {checked_vars}")
        for var, (line, col) in allocated_vars.items():
            if var not in checked_vars:
                issues.append(
                    Issue(
                        file=filename,
                        line=line,
                        column=col,
                        message=f"Potential failure from allocation assigned to '{var}' is not checked.",
                    )
                )


def _get_nolint_lines(source_code: str) -> Tuple[Dict[int, Set[str]], bool]:
    """Identify lines marked with NOLINT comments.

    Args:
        source_code (str): The C source code.

    Returns:
        Tuple[Dict[int, Set[str]], bool]: A tuple containing the dictionary of ignored lines mapping to their scopes, and a boolean indicating if the entire file should be ignored.
    """
    ignored_lines: Dict[int, Set[str]] = {}
    ignore_file = False

    pattern = re.compile(
        r"(?:NOLINT(?!NEXTLINE)|c-linter-disable(?!-file))(?:\(([^)]+)\))?"
    )
    pattern_next = re.compile(r"NOLINTNEXTLINE(?:\(([^)]+)\))?")

    for i, line in enumerate(source_code.splitlines()):
        line_num = i + 1
        if "NOLINTFILE" in line or "c-linter-disable-file" in line:
            ignore_file = True

        for match in pattern.finditer(line):
            scopes = match.group(1)
            scope_set = {s.strip() for s in scopes.split(",")} if scopes else {"*"}
            if line_num not in ignored_lines:
                ignored_lines[line_num] = set()
            ignored_lines[line_num].update(scope_set)

        for match in pattern_next.finditer(line):
            scopes = match.group(1)
            scope_set = {s.strip() for s in scopes.split(",")} if scopes else {"*"}
            target_line = line_num + 1
            if target_line not in ignored_lines:
                ignored_lines[target_line] = set()
            ignored_lines[target_line].update(scope_set)

            # Also ignore the line with NOLINTNEXTLINE itself to suppress C89 comment warnings
            if line_num not in ignored_lines:
                ignored_lines[line_num] = set()
            ignored_lines[line_num].add("*")

    return ignored_lines, ignore_file


def _get_issue_scope(msg: str) -> str:
    """Map a diagnostic message to a suppressible scope."""
    if "Use safe CRT alternative" in msg:
        return "safe-crt"
    if "is discarded. Must be assigned" in msg:
        return "discarded-return"
    if "Format specifier" in msg and "Windows #ifdef guard" in msg:
        return "windows-format"
    if "is not checked" in msg and "allocation" in msg:
        return "unchecked-allocation"
    if "returns non-compliant type" in msg:  # pragma: no cover
        return "return-type"  # pragma: no cover
    return "compiler-diagnostic"


def _filter_diagnostics(
    issues: List[Issue],
    nolint_lines: Dict[int, Set[str]],
    ignore_file: bool,
    std: str,
    tolerate_c99_types: bool,
    ignore_missing_includes: bool = False,
) -> List[Issue]:
    """Filter issues based on NOLINT lines and standard tolerances."""
    if ignore_file:
        return []
    filtered = []
    for issue in issues:
        if issue.line in nolint_lines:
            scopes = nolint_lines[issue.line]
            if "*" in scopes or _get_issue_scope(issue.message) in scopes:
                continue

        msg = issue.message
        if ignore_missing_includes and "file not found" in msg:  # pragma: no cover
            continue  # pragma: no cover

        if tolerate_c99_types and std == "c89":
            if (
                "'_Bool' is a C99 extension" in msg
                or "'long long' is an extension" in msg
                or "variadic macros are a C99 feature" in msg
                or "extension used" in msg
            ):
                continue

        filtered.append(issue)
    return filtered


def lint_file(
    filename: str,
    check_windows: bool = True,
    check_safe_crt: bool = True,
    strict_safe_crt: bool = False,
    includes: Optional[List[str]] = None,
    ignore_returns: Optional[List[str]] = None,
    std: str = "c89",
    build_dir: str = "",
    tolerate_c99_types: bool = True,
    header_only_strategy: bool = True,
    ignore_missing_includes: bool = False,
    no_test_relaxations: bool = False,
    freestanding: bool = False,
    max_issues_per_file: int = 50,
    fix_issues: bool = False,
    no_pedantic: bool = False,
) -> List[Issue]:
    """Lint a C file and return a list of identified issues.

    Args:
        filename (str): The path to the C file to lint.
        check_windows (bool): Whether to enforce Windows #ifdef guards for format strings.
        check_safe_crt (bool): Whether to enforce Safe CRT alternatives.
        strict_safe_crt (bool): Whether to enforce strict Safe CRT alternatives (e.g. strncpy).
        includes (Optional[List[str]]): Additional include directories.
        ignore_returns (Optional[List[str]]): List of macros/functions to ignore discarded returns for.
        std (str): C standard version (e.g., c89, c99).
        build_dir (str): Path to build directory containing compile_commands.json.
        tolerate_c99_types (bool): Whether to tolerate C99 type extensions in C89 mode.
        header_only_strategy (bool): Whether to inject common includes for standalone headers.
        ignore_missing_includes (bool): Suppress 'file not found' diagnostics.
        no_test_relaxations (bool): Disable relaxed rules for test files.
        freestanding (bool): Enforce a freestanding environment (disables built-in headers).

    Returns:
        List[Issue]: A list of linting issues found in the file.
    """
    if includes is None:
        includes = []
    if ignore_returns is None:
        ignore_returns = []

    is_test_file = False
    if not no_test_relaxations:
        from pathlib import Path

        path_obj = Path(filename)
        if (
            "tests" in path_obj.parts
            or "test" in path_obj.parts
            or "examples" in path_obj.parts
            or path_obj.name.startswith("test_")
        ):
            is_test_file = True

    if is_test_file:
        ignore_missing_includes = True
        tolerate_c99_types = True
        check_safe_crt = False
        strict_safe_crt = False
        ignore_returns.append("*")

    clang_args = [
        f"-std={std}",
        "-pedantic",
        "-Wall",
        "-Wno-unused-variable",
        "-ferror-limit=0",
    ]
    if freestanding:
        clang_args.append("-ffreestanding")
    for inc in includes:
        clang_args.append(f"-I{inc}")

    if header_only_strategy and filename.endswith(".h"):
        base_name = os.path.basename(filename).lower()
        if not base_name.endswith("stdbool.h"):
            clang_args.extend(["-include", "stdbool.h"])
        if not base_name.endswith("stdint.h"):
            clang_args.extend(["-include", "stdint.h"])
        if not base_name.endswith("stddef.h"):
            clang_args.extend(["-include", "stddef.h"])
        clang_args.append("-Wno-unused-function")

    if build_dir:
        try:
            compdb = CompilationDatabase.fromDirectory(build_dir)
            abs_filename = os.path.abspath(filename)
            cmds = compdb.getCompileCommands(abs_filename)
            if cmds and len(cmds) > 0:
                extracted = _extract_clang_args(list(cmds[0].arguments))
                clang_args.extend(extracted)
        except CompilationDatabaseError:
            pass

    index = Index.create()
    tu = index.parse(filename, args=clang_args)

    issues, implicit_funcs = _get_diagnostics(
        tu,
        filename,
        ignore_missing_includes,
        max_issues_per_file,
        fix_issues,
        no_pedantic,
    )

    # Ignore implicitly declared functions if ignore_missing_includes is true
    if ignore_missing_includes and implicit_funcs:  # pragma: no cover
        ignore_returns.extend(implicit_funcs)  # pragma: no cover

    try:
        with open(filename, "r", encoding="utf-8") as f:
            source_code: str = f.read()
    except Exception:  # pragma: no cover
        source_code = ""

    safe_crt_guarded_lines: Set[int] = (
        _get_guarded_lines(source_code, SAFE_CRT_MACROS)
        if check_safe_crt and source_code
        else set()
    )

    _analyze_ast(
        tu.cursor,
        filename,
        issues,
        check_safe_crt,
        strict_safe_crt,
        safe_crt_guarded_lines,
        ignore_returns,
        source_code=source_code,
    )
    _check_allocations(tu.cursor, filename, issues)

    if check_windows and source_code:
        _check_windows_format_literals(source_code, filename, issues)

    nolint_lines, ignore_file = _get_nolint_lines(source_code)
    return _filter_diagnostics(
        issues,
        nolint_lines,
        ignore_file,
        std,
        tolerate_c99_types,
        ignore_missing_includes,
    )


def lint_code(
    code: str,
    filename: str = "memory.c",
    check_windows: bool = True,
    check_safe_crt: bool = True,
    strict_safe_crt: bool = False,
    includes: Optional[List[str]] = None,
    ignore_returns: Optional[List[str]] = None,
    std: str = "c89",
    tolerate_c99_types: bool = True,
    header_only_strategy: bool = True,
    ignore_missing_includes: bool = False,
    no_test_relaxations: bool = False,
    freestanding: bool = False,
    max_issues_per_file: int = 50,
    fix_issues: bool = False,
    no_pedantic: bool = False,
) -> List[Issue]:
    """Lint a string containing C code and return a list of identified issues.

    Args:
        code (str): The C source code to lint.
        filename (str): A virtual filename to assign to the code snippet.
        check_windows (bool): Whether to enforce Windows #ifdef guards for format strings.
        check_safe_crt (bool): Whether to enforce Safe CRT alternatives.
        strict_safe_crt (bool): Whether to enforce strict Safe CRT alternatives.
        includes (Optional[List[str]]): Additional include directories.
        ignore_returns (Optional[List[str]]): List of macros/functions to ignore discarded returns for.
        std (str): C standard version (e.g., c89, c99).
        tolerate_c99_types (bool): Whether to tolerate C99 type extensions in C89 mode.
        header_only_strategy (bool): Whether to inject common includes for standalone headers.
        ignore_missing_includes (bool): Suppress 'file not found' diagnostics.
        no_test_relaxations (bool): Disable relaxed rules for test files.
        freestanding (bool): Enforce a freestanding environment (disables built-in headers).

    Returns:
        List[Issue]: A list of linting issues found in the code.
    """
    if includes is None:
        includes = []
    if ignore_returns is None:
        ignore_returns = []

    is_test_file = False
    if not no_test_relaxations:
        from pathlib import Path

        path_obj = Path(filename)
        if (
            "tests" in path_obj.parts
            or "test" in path_obj.parts
            or "examples" in path_obj.parts
            or path_obj.name.startswith("test_")
        ):
            is_test_file = True

    if is_test_file:
        ignore_missing_includes = True
        tolerate_c99_types = True
        check_safe_crt = False
        strict_safe_crt = False
        ignore_returns.append("*")

    index = Index.create()

    clang_args = [
        f"-std={std}",
        "-pedantic",
        "-Wall",
        "-Wno-unused-variable",
        "-ferror-limit=0",
    ]
    if freestanding:
        clang_args.append("-ffreestanding")
    for inc in includes:
        clang_args.append(f"-I{inc}")

    if header_only_strategy and filename.endswith(".h"):
        base_name = os.path.basename(filename).lower()
        if not base_name.endswith("stdbool.h"):
            clang_args.extend(["-include", "stdbool.h"])
        if not base_name.endswith("stdint.h"):
            clang_args.extend(["-include", "stdint.h"])
        if not base_name.endswith("stddef.h"):
            clang_args.extend(["-include", "stddef.h"])
        clang_args.append("-Wno-unused-function")

    tu = index.parse(
        filename,
        args=clang_args,
        unsaved_files=[(filename, code)],
    )

    issues, implicit_funcs = _get_diagnostics(
        tu,
        filename,
        ignore_missing_includes,
        max_issues_per_file,
        fix_issues,
        no_pedantic,
    )

    if ignore_missing_includes and implicit_funcs:  # pragma: no cover
        ignore_returns.extend(implicit_funcs)  # pragma: no cover

    safe_crt_guarded_lines: Set[int] = (
        _get_guarded_lines(code, SAFE_CRT_MACROS) if check_safe_crt else set()
    )
    _analyze_ast(
        tu.cursor,
        filename,
        issues,
        check_safe_crt,
        strict_safe_crt,
        safe_crt_guarded_lines,
        ignore_returns,
        source_code=code,
    )
    _check_allocations(tu.cursor, filename, issues)

    if check_windows:
        _check_windows_format_literals(code, filename, issues)

    nolint_lines, ignore_file = _get_nolint_lines(code)
    return _filter_diagnostics(
        issues,
        nolint_lines,
        ignore_file,
        std,
        tolerate_c99_types,
        ignore_missing_includes,
    )
