"""Tests for the C Linter core logic."""

import pytest
from clang.cindex import TranslationUnitLoadError  # type: ignore
from c_linter import lint_code, lint_file, Issue


def test_models_issue_str():
    """Test the formatting of the Issue string."""
    issue = Issue(file="test.c", line=10, column=5, message="test error")
    assert str(issue) == "test.c:10:5: test error"


def test_c89_compliance():
    """Test that C89 violations are correctly flagged."""
    code = """
int main(void) {
    int a = 1;
    a = 2;
    int b = 3;
    return a + b;
}
"""
    issues = lint_code(code)
    assert any(
        "mixing declarations and code is a C99 extension" in str(i) for i in issues
    )


def test_return_types_compliant():
    """Test compliant return types."""
    code = """
enum Status { OK, ERROR };
void func1(void) {}
int func2(void) { return 0; }
enum Status func3(void) { return OK; }
double func4(void) { return 0.0; }
char* ptr_func(void) { return 0; }
struct Point { int x; int y; };
struct Point struct_func(void) { struct Point p; p.x=0; p.y=0; return p; }
int main(void) { return 0; }
"""
    issues = lint_code(code)
    # The return type messages should not be present
    assert not any("returns non-compliant type" in str(i) for i in issues)


def test_return_types_non_compliant():
    """Test non-compliant return types."""
    code = """
typedef struct { int a; } SomeStruct;
SomeStruct my_func() { SomeStruct s; s.a = 1; return s; }
"""
    # Wait, RECORD is allowed.
    # What's not allowed? How about we return a complex type or an array? Arrays can't be returned by value.
    # What about a custom typedef that resolves to something weird? Or just a _Complex type?
    code = """
_Complex double bad_func(void) { return 0; }
"""
    issues = lint_code(code)
    assert any("returns non-compliant type" in str(i) for i in issues)


def test_nodiscard_compliant():
    """Test nodiscard on int-returning function being compliant."""
    code = """
int do_something(void) { return 1; }
int main(void) {
    int res = do_something();
    if (do_something()) { return 1; }
    while(do_something()) { break; }
    return res;
}
"""
    issues = lint_code(code)
    assert not any("is discarded. Must be assigned." in str(i) for i in issues)


def test_nodiscard_non_compliant():
    """Test nodiscard violations."""
    code = """
int do_something(void) { return 1; }
int main(void) {
    do_something(); /* Discarded */
    (void)do_something(); /* Cast to void is now allowed */
    return 0;
}
"""
    issues = lint_code(code)
    assert any("discarded. Must be assigned" in str(i) for i in issues)
    assert not any("cast to void. Must be assigned" in str(i) for i in issues)


def test_allocation_compliant():
    """Test compliant allocations."""
    code = """
#include <stdlib.h>
int main(void) {
    void *p = malloc(10);
    if (!p) return 1;
    
    void *q = calloc(1, 10);
    if (q == NULL) return 1;

    void *r = realloc(p, 20);
    if (NULL != r) { }
    
    return 0;
}
"""
    issues = lint_code(code)
    assert not any("Potential failure from allocation" in str(i) for i in issues)


def test_deref_before_check():
    """Test allocations that are dereferenced before checking."""
    code = """
#include <stdlib.h>
struct S { int val; };
int main(void) {
    int *p = malloc(10);
    *p = 1; /* Dereference via * */
    int *q = malloc(10);
    q[0] = 1; /* Dereference via [] */
    struct S *s = malloc(sizeof(struct S));
    s->val = 1; /* Dereference via -> */
    return 0;
}
"""
    issues = lint_code(code)
    assert any(
        "Potential failure from allocation assigned to 'p' is not checked." in str(i)
        for i in issues
    )
    assert any(
        "Potential failure from allocation assigned to 'q' is not checked." in str(i)
        for i in issues
    )
    assert any(
        "Potential failure from allocation assigned to 's' is not checked." in str(i)
        for i in issues
    )


def test_fix_issues(tmp_path):
    """Test --fix logic for missing newlines."""
    p = tmp_path / "nonewline.c"
    p.write_text("int main(void) { return 0; }", encoding="utf-8")

    from c_linter.linter import lint_file

    issues = lint_file(str(p), fix_issues=True)
    assert any("no newline at end of file" in str(i) for i in issues)
    assert any(getattr(i, "fixed", False) for i in issues)

    content = p.read_text(encoding="utf-8")
    assert content.endswith("\n")

    from unittest.mock import patch

    with patch("builtins.open", side_effect=Exception("Perm denied")):
        issues = lint_file(str(p), fix_issues=True)
        assert not any("no newline at end of file" in str(i) for i in issues)


def test_no_pedantic(tmp_path):
    """Test --no-pedantic disables trivial formatting warnings."""
    p = tmp_path / "nonewline.c"
    p.write_text("int main(void) { return 0; }", encoding="utf-8")

    from c_linter.linter import lint_file

    issues = lint_file(str(p))
    assert any("no newline at end of file" in str(i) for i in issues)

    issues = lint_file(str(p), no_pedantic=True)
    assert not any("no newline at end of file" in str(i) for i in issues)


def test_allocation_non_compliant():
    """Test non-compliant allocations."""
    code = """
#include <stdlib.h>
int main(void) {
    void *p;
    p = malloc(10); /* not checked */
    void *q = calloc(1, 10); /* not checked */
    return 0;
}
"""
    issues = lint_code(code)
    assert (
        sum(
            "Potential failure from allocation assigned to 'p' is not checked" in str(i)
            for i in issues
        )
        == 1
    )
    assert (
        sum(
            "Potential failure from allocation assigned to 'q' is not checked" in str(i)
            for i in issues
        )
        == 1
    )


def test_safe_crt_non_compliant():
    code = """
int main(void) {
    fopen("test.txt", "r");
    return 0;
}
"""
    issues = lint_code(code)
    assert sum("Use safe CRT alternative for 'fopen'" in str(i) for i in issues) == 1


def test_safe_crt_guarded():
    code = """
int main(void) {
#ifdef __STDC_WANT_LIB_EXT1__
    fopen_s(&f, "test.txt", "r");
#else
    fopen("test.txt", "r");
#endif
    return 0;
}
"""
    issues = lint_code(code)
    assert not any("Use safe CRT alternative for 'fopen'" in str(i) for i in issues)
    code = """
int main(void) {
    fopen("test.txt", "r");
    return 0;
}
"""
    issues = lint_code(code, check_safe_crt=False)
    assert not any("Use safe CRT alternative for 'fopen'" in str(i) for i in issues)


def test_windows_format_unguarded():
    code = """
int main(void) {
    char *s = "%I64d";
    return 0;
}
"""
    issues = lint_code(code)
    assert (
        sum(
            "Format specifier (I64/I) used without Windows #ifdef guard." in str(i)
            for i in issues
        )
        == 1
    )


def test_windows_format_guarded_ifdef():
    code = """
int main(void) {
#ifdef _WIN32
    char *s = "%Iu";
#else
    char *s = "%zu";
#endif
    return 0;
}
"""
    issues = lint_code(code)
    assert not any("used without Windows #ifdef guard" in str(i) for i in issues)


def test_windows_format_guarded_if_defined():
    code = """
int main(void) {
#if defined(WIN32)
    char *s = "%I64d";
#endif
    return 0;
}
"""
    issues = lint_code(code)
    assert not any("used without Windows #ifdef guard" in str(i) for i in issues)


def test_windows_format_guarded_other_macros():
    code = """
int main(void) {
#ifdef __MINGW64__
    char *s1 = "%I64d";
#endif
#if defined(__CYGWIN__) || defined(_WIN64)
    char *s2 = "%Iu";
#endif
    return 0;
}
"""
    issues = lint_code(code)
    assert not any("used without Windows #ifdef guard" in str(i) for i in issues)


def test_windows_format_disabled():
    code = """
int main(void) {
    char *s = "%zu";
    return 0;
}
"""
    issues = lint_code(code, check_windows=False)
    assert not any("used without Windows #ifdef guard" in str(i) for i in issues)


def test_lint_file_wrapper(tmp_path):
    """Test the lint_file wrapper."""
    p = tmp_path / "test.c"
    p.write_text("int main(void) { return 0; }\n")
    issues = lint_file(str(p))
    assert not issues


def test_lint_file_io_error():
    """Test the lint_file wrapper handling file read errors gracefully."""
    with pytest.raises(TranslationUnitLoadError):
        lint_file("does_not_exist_file.c")


def test_extract_clang_args():
    from c_linter.linter import _extract_clang_args

    assert _extract_clang_args([]) == []
    assert _extract_clang_args(
        [
            "gcc",
            "main.c",
            "-I",
            "include",
            "-isystem",
            "sys",
            "-DNDEBUG",
            "-U_WIN32",
            "-std=c99",
            "-m32",
            "-O3",
        ]
    ) == [
        "-I",
        "include",
        "-isystem",
        "sys",
        "-DNDEBUG",
        "-U_WIN32",
        "-std=c99",
        "-m32",
    ]


def test_get_diagnostics_unknown_file():
    from c_linter.linter import _get_diagnostics
    from unittest.mock import MagicMock

    tu = MagicMock()
    diag = MagicMock()
    diag.location.file = None
    tu.diagnostics = [diag]
    assert _get_diagnostics(tu, "main.c")[0] == []


def test_error_capping():
    from unittest.mock import MagicMock
    from c_linter.linter import _get_diagnostics

    tu = MagicMock()
    diags = []
    for i in range(55):
        diag = MagicMock()
        diag.location.file.name = "main.c"
        diag.location.line = i
        diag.location.column = 1
        diag.spelling = f"Error {i}"
        diags.append(diag)
    tu.diagnostics = diags
    issues, _ = _get_diagnostics(tu, "main.c")
    assert len(issues) == 51
    assert (
        issues[-1].message
        == "Too many compiler diagnostics generated. Truncating output to prevent cascading cascades."
    )
    code = """
int main(void) {
    undeclared_func();
    return 0;
}
"""
    issues = lint_code(code)
    # The linter shouldn't complain about discarded returns for implicit functions
    assert not any("discarded" in str(i) for i in issues)


def test_ignore_missing_includes_cascade():
    from unittest.mock import MagicMock
    from c_linter.linter import _get_diagnostics

    tu = MagicMock()

    diag1 = MagicMock()
    diag1.location.file.name = "main.c"
    diag1.spelling = "unknown type name 'cdd_c_ir_t'"

    diag2 = MagicMock()
    diag2.location.file.name = "main.c"
    diag2.spelling = "implicit declaration of function 'jasprintf'"

    tu.diagnostics = [diag1, diag2]

    issues, implicit_funcs = _get_diagnostics(
        tu, "main.c", ignore_missing_includes=True
    )
    assert len(issues) == 0
    assert "jasprintf" in implicit_funcs


def test_read_source_code_exception(tmp_path):
    p = tmp_path / "test.c"
    p.write_text("int main(void) { return 0; }\n")
    from unittest.mock import patch

    with patch("builtins.open", side_effect=Exception):
        issues = lint_file(str(p))
    assert not issues


def test_ignored_returns_wildcard():
    code = """
int WinHttp_DoSomething(void) { return 1; }
int c_rest_call(void) { return 1; }
int main(void) {
    WinHttp_DoSomething();
    c_rest_call();
    return 0;
}
"""
    issues = lint_code(code, ignore_returns=["WinHttp*", "c_rest*"])
    assert not issues
    code = """
#include <stdio.h>
int main(void) {
    printf("test");
    ASSERT_EQ(1, 1);
    LOG_INFO("test");
    return 0;
}
"""
    issues = lint_code(code)
    assert not any("discarded" in str(i) for i in issues)


def test_ignored_returns_exception():
    code = """
int do_something(void) { return 1; }
int main(void) {
    do_something();
    return 0;
}
"""
    # Cause exception by using an invalid regex to trigger the exception branch
    from unittest.mock import patch

    with patch("re.match", side_effect=Exception):
        issues = lint_code(code)
    assert any("discarded" in str(i) for i in issues)


def test_lint_file_none_defaults(tmp_path):
    p = tmp_path / "test.c"
    p.write_text("int main(void) { return 0; }\n")
    issues = lint_file(str(p), includes=["test_include"], ignore_returns=None)
    assert not issues


def test_lint_file_test_relaxations(tmp_path):
    p = tmp_path / "test_example.c"
    p.write_text(
        "int do_something() { return 1; } int main(void) { do_something(); return 0; }\n"
    )
    issues = lint_file(str(p))
    # Nodiscard should be ignored
    assert not any("discarded" in i.message for i in issues)


def test_lint_code_freestanding():
    issues = lint_code("int main(void) { return 0; }\n", freestanding=True)
    assert not issues


def test_lint_file_freestanding(tmp_path):
    p = tmp_path / "main.c"
    p.write_text("int main(void) { return 0; }\n")
    issues = lint_file(str(p), freestanding=True)
    assert not issues


def test_lint_code_none_defaults():
    issues = lint_code(
        "int main(void) { return 0; }\n", includes=["test_include"], ignore_returns=None
    )
    assert not issues


def test_macro_ignored_returns():
    code = """
int my_func(int x);
#define ASSERT_MACRO(x) my_func(x)
int main(void) {
    ASSERT_MACRO(1);
    return 0;
}
"""
    issues = lint_code(code)
    assert not any("discarded" in str(i) for i in issues)


def test_compdb_load(tmp_path):
    import os

    p = tmp_path / "test.c"
    p.write_text("int main(void) { return 0; }\n")
    b = tmp_path / "build"
    b.mkdir()
    compdb_path = b / "compile_commands.json"
    abs_path = os.path.abspath(str(p)).replace("\\", "/")
    compdb_path.write_text(
        f'[{{"directory": "/", "command": "gcc -Iinclude {abs_path}", "file": "{abs_path}"}}]'
    )
    issues = lint_file(str(p), build_dir=str(b))
    assert not issues


def test_compdb_error(tmp_path):
    """Test CompilationDatabase error."""
    p = tmp_path / "test.c"
    p.write_text("int main(void) { return 0; }\n")
    b = tmp_path / "build2"
    b.mkdir()
    # No compile commands, raises CompilationDatabaseError
    issues = lint_file(str(p), build_dir=str(b))
    assert not issues


def test_nolint():
    code = """
int main(void) {
    fopen("test", "r"); // NOLINT
    fopen("test", "r");
    /* c-linter-disable */
    return 0;
}
"""
    issues = lint_code(code)
    # Line 3 is suppressed, line 4 emits 2 issues (discarded return, safe CRT)
    assert len([i for i in issues if "fopen" in i.message]) == 2
    assert not any(i.line == 3 for i in issues)


def test_scoped_nolint():
    code = """
int do_something(void) { return 1; }
int main(void) {
    do_something(); fopen("test", "r"); /* NOLINT(safe-crt) */
    return 0;
}
"""
    issues = lint_code(code)
    # The return value is discarded, so we should get 'discarded-return', but not safe-crt
    assert len(issues) >= 1
    messages = [issue.message for issue in issues]
    assert any("is discarded" in msg for msg in messages)
    assert not any("safe CRT" in msg for msg in messages)


def test_nolintfile():
    code = """
// NOLINTFILE
int main(void) {
    fopen("test", "r");
    return 0;
}
"""
    issues = lint_code(code)
    assert not issues


def test_scoped_nolint_types():
    code = """
#include <stdio.h>
#include <stdlib.h>
void* test_fn(void) { /* NOLINT(return-type) */
    void *p = malloc(10); /* NOLINT(unchecked-allocation) */
    printf("%I64d"); /* NOLINT(windows-format, compiler-diagnostic) */
    int a = 1; /* NOLINT(compiler-diagnostic) */
    return p;
}
"""
    # Needs to ignore windows format warning and unchecked allocation
    issues = lint_code(code, check_windows=True, std="c89", check_safe_crt=False)
    # the function has return type warning suppressed, unchecked allocation suppressed
    assert not issues


def test_tolerate_c99_extensions():
    code = """
_Bool b = 1;
long long a = 0;
"""
    # by default tolerate_c99_types is True
    issues = lint_code(code, std="c89")
    assert not any(
        "C99 extension" in i.message or "long long" in i.message for i in issues
    )

    # if False
    issues = lint_code(code, std="c89", tolerate_c99_types=False)
    assert any("extension" in i.message for i in issues)


def test_missing_includes_and_implicit_funcs():
    code = """
#include <doesnotexist.h>
int main(void) {
    some_implicit_func();
    return 0;
}
"""
    # test with ignore missing includes
    issues = lint_code(code, ignore_missing_includes=True)
    assert not any("file not found" in str(i) for i in issues)
    assert not any("discarded" in str(i) for i in issues)


def test_header_strategy(tmp_path):
    p = tmp_path / "test.h"
    p.write_text("bool a = true;\n")
    # should inject stdbool.h
    issues = lint_file(str(p))
    assert not any("unknown type name 'bool'" in i.message for i in issues)

    # disable strategy
    issues = lint_file(str(p), header_only_strategy=False)
    assert any("unknown type name 'bool'" in i.message for i in issues)

    # Test lint_code with .h extension
    issues = lint_code("bool a = true;\n", filename="memory.h")
    assert not any("unknown type name 'bool'" in i.message for i in issues)


def test_analyze_ast_value_error():
    from c_linter.linter import _analyze_ast
    from unittest.mock import MagicMock
    from clang.cindex import CursorKind, TypeKind

    cursor1 = MagicMock()
    cursor1.kind = CursorKind.FUNCTION_DECL
    cursor1.is_definition.return_value = True
    cursor1.location.file.name = "main.c"

    # Mocking result_type.kind to return something not in allowed_types
    # and canonical_kind to raise ValueError
    class MockTypeCanonical:
        @property
        def kind(self):
            raise ValueError()

    class MockType:
        @property
        def kind(self):
            return TypeKind.INVALID

        def get_canonical(self):
            return MockTypeCanonical()

    cursor1.result_type = MockType()

    issues = []
    # This shouldn't crash
    _analyze_ast(cursor1, "main.c", issues, False, False, set(), [])

    cursor2 = MagicMock()
    cursor2.kind = CursorKind.CALL_EXPR
    cursor2.location.file.name = "main.c"

    class MockTypeCanonical2:
        def get_canonical(self):
            return MockType()

    cursor2.type = MockTypeCanonical2()

    # This shouldn't crash
    _analyze_ast(cursor2, "main.c", issues, False, False, set(), [])


def test_implicit_declaration_ignore(tmp_path):
    p = tmp_path / "test.c"
    p.write_text("void foo() { my_implicit_func(10); }\n")
    issues = lint_file(str(p), ignore_missing_includes=True)
    assert not any("discarded" in str(i) for i in issues)


def test_lint_code_is_test_file():
    code = "int my_implicit(int x); int main(void) { my_implicit(1); return 0; }"
    issues = lint_code(code, filename="test_main.c")
    assert not any("discarded" in str(i) for i in issues)


def test_ignored_returns_fallback():
    code = """
int MY_CUSTOM_ASSERT(void) { return 1; }
int SOMETHING_FREE(void) { return 1; }
int main(void) {
    MY_CUSTOM_ASSERT();
    SOMETHING_FREE();
    return 0;
}
"""
    issues = lint_code(code)
    assert not any("discarded" in str(i) for i in issues)


def test_diagnostic_limit_and_grouping():
    code = "int main(void) { unknown_type a; unknown_type b; unknown_type c; unknown_type d; unknown_type e; unknown_type f; unknown_type g; return 0; }"
    issues = lint_code(code)
    counts = sum(1 for i in issues if "unknown type name" in i.message)
    assert counts <= 5


def test_fix_issues_exception(tmp_path):
    from unittest.mock import patch

    p = tmp_path / "nonewline.c"
    p.write_text("int main(void) { return 0; }", encoding="utf-8")
    with patch("builtins.open", side_effect=Exception("Failed to open")):
        issues = lint_file(str(p), fix_issues=True)
    assert any("no newline at end of file" in str(i) for i in issues)


def test_no_pedantic_flag(tmp_path):
    p = tmp_path / "nonewline.c"
    p.write_text("int main(void) { return 0; }", encoding="utf-8")
    issues = lint_file(str(p), no_pedantic=True)
    assert not any("no newline at end of file" in str(i) for i in issues)


def test_has_missing_include_pass():
    code = "#include <nonexistent.h>\nint main(void) { return 0; }"
    issues = lint_code(code, ignore_missing_includes=False)
    assert any("file not found" in str(i) for i in issues)


def test_ignore_returns_suffixes():
    code = "int my_free(void) { return 1; } int main(void) { my_free(); return 0; }"
    issues = lint_code(code)
    assert not any("discarded. Must be assigned" in str(i) for i in issues)


def test_unchecked_allocation_msg():
    from c_linter.linter import _get_issue_scope

    assert (
        _get_issue_scope("allocation assigned to 'x' is not checked")
        == "unchecked-allocation"
    )
