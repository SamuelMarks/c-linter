"""Core linting logic utilizing libclang for AST analysis."""

import os
import re
from typing import List, Optional, Set, Dict, Tuple, Iterable, Any

# We ignore missing types from clang as it does not ship with a py.typed marker.
from clang.cindex import Index, Cursor, CursorKind, TypeKind, TranslationUnit  # type: ignore

from .models import Issue


def _get_diagnostics(tu: TranslationUnit) -> List[Issue]:
    """Extract standard clang diagnostics (including C89 violations).

    Args:
        tu (TranslationUnit): The parsed translation unit.

    Returns:
        List[Issue]: A list of issues representing compiler warnings and errors.
    """
    issues: List[Issue] = []
    for diag in tu.diagnostics:
        filename: str = diag.location.file.name if diag.location.file else "unknown"
        issues.append(
            Issue(
                file=filename,
                line=diag.location.line,
                column=diag.location.column,
                message=diag.spelling,
            )
        )
    return issues


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
    format_specifiers_re: re.Pattern[str] = re.compile(
        r"%(?:z[udixX]|ll[udixX]|I64[udixX]|I[udixX])"
    )

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
                            message="Format specifier (size_t/long long) used without Windows #ifdef guard.",
                        )
                    )


def _analyze_ast(
    cursor: Cursor,
    filename: str,
    issues: List[Issue],
    check_safe_crt: bool,
    safe_crt_guarded_lines: Set[int],
    parent: Optional[Cursor] = None,
) -> None:
    """Recursively analyze the AST for return type, nodiscard, and Safe CRT violations.

    Args:
        cursor (Cursor): The current AST node being analyzed.
        filename (str): The primary filename being linted (to ignore headers).
        issues (List[Issue]): The list to append found issues to.
        check_safe_crt (bool): Whether to enforce safe CRT functions.
        safe_crt_guarded_lines (Set[int]): Lines that are exempted from Safe CRT checks.
        parent (Optional[Cursor]): The parent node of the current cursor.
    """
    if cursor.location.file and os.path.abspath(
        cursor.location.file.name
    ) != os.path.abspath(filename):
        return

    # Check 1: Return types of user-defined functions
    if cursor.kind == CursorKind.FUNCTION_DECL and cursor.is_definition():
        ret_type = cursor.result_type.kind
        # Allow void, int, enums, and math types (float, double, long double)
        allowed_types: Set[Any] = {
            TypeKind.VOID,
            TypeKind.INT,
            TypeKind.ENUM,
            TypeKind.FLOAT,
            TypeKind.DOUBLE,
            TypeKind.LONGDOUBLE,
        }
        if ret_type not in allowed_types:
            canonical_kind = cursor.result_type.get_canonical().kind
            if canonical_kind not in allowed_types:
                msg: str = (
                    f"Function '{cursor.spelling}' returns non-compliant type "
                    f"({canonical_kind.name}). Must return int, enum, void, or math type."
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
        # Check 2: Nodiscard focus on int functions
        if cursor.type.get_canonical().kind == TypeKind.INT:
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
            elif (
                parent
                and parent.kind == CursorKind.CSTYLE_CAST_EXPR
                and parent.type.kind == TypeKind.VOID
            ):
                msg = f"Call to int-returning function '{cursor.spelling}' is cast to void. Must be assigned."
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
            safe_crt_guarded_lines,
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

        def find_decl_refs(c: Cursor) -> None:
            """Recursively find and record declaration references.

            Args:
                c (Cursor): The AST node to check.
            """
            if c.kind == CursorKind.DECL_REF_EXPR:
                checked_vars.add(c.spelling)
            for child in c.get_children():
                find_decl_refs(child)

        if cursor.kind == CursorKind.UNARY_OPERATOR:
            tokens = list(cursor.get_tokens())
            if tokens and tokens[0].spelling == "!":
                for child in cursor.get_children():
                    find_decl_refs(child)

        # Relational operators in C return INT (e.g., ==, !=, <, >)
        if (
            cursor.kind == CursorKind.BINARY_OPERATOR
            and cursor.type.get_canonical().kind == TypeKind.INT
        ):
            for child in cursor.get_children():
                find_decl_refs(child)

        # Direct condition checks (if (p))
        if cursor.kind in (CursorKind.IF_STMT, CursorKind.WHILE_STMT):
            children = list(cursor.get_children())
            if children:
                find_decl_refs(children[0])

        for child in cursor.get_children():
            walk_allocs_and_checks(child, cursor, allocated_vars, checked_vars)

    for func in find_function_decls(tu_cursor):
        allocated_vars: Dict[str, Tuple[int, int]] = {}
        checked_vars: Set[str] = set()
        walk_allocs_and_checks(func, None, allocated_vars, checked_vars)
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


def lint_file(
    filename: str, check_windows: bool = True, check_safe_crt: bool = True
) -> List[Issue]:
    """Lint a C file and return a list of identified issues.

    Args:
        filename (str): The path to the C file to lint.
        check_windows (bool): Whether to enforce Windows #ifdef guards for format strings.
        check_safe_crt (bool): Whether to enforce Safe CRT alternatives.

    Returns:
        List[Issue]: A list of linting issues found in the file.
    """
    index = Index.create()
    tu = index.parse(
        filename, args=["-std=c89", "-pedantic", "-Wall", "-Wno-unused-variable"]
    )

    issues: List[Issue] = _get_diagnostics(tu)

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

    _analyze_ast(tu.cursor, filename, issues, check_safe_crt, safe_crt_guarded_lines)
    _check_allocations(tu.cursor, filename, issues)

    if check_windows and source_code:
        _check_windows_format_literals(source_code, filename, issues)

    return issues


def lint_code(
    code: str,
    filename: str = "memory.c",
    check_windows: bool = True,
    check_safe_crt: bool = True,
) -> List[Issue]:
    """Lint a string containing C code and return a list of identified issues.

    Args:
        code (str): The C source code to lint.
        filename (str): A virtual filename to assign to the code snippet.
        check_windows (bool): Whether to enforce Windows #ifdef guards for format strings.
        check_safe_crt (bool): Whether to enforce Safe CRT alternatives.

    Returns:
        List[Issue]: A list of linting issues found in the code.
    """
    index = Index.create()
    tu = index.parse(
        filename,
        args=["-std=c89", "-pedantic", "-Wall", "-Wno-unused-variable"],
        unsaved_files=[(filename, code)],
    )

    issues: List[Issue] = _get_diagnostics(tu)

    safe_crt_guarded_lines: Set[int] = (
        _get_guarded_lines(code, SAFE_CRT_MACROS) if check_safe_crt else set()
    )
    _analyze_ast(tu.cursor, filename, issues, check_safe_crt, safe_crt_guarded_lines)
    _check_allocations(tu.cursor, filename, issues)

    if check_windows:
        _check_windows_format_literals(code, filename, issues)

    return issues
