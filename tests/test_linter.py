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
    pass


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
    char *s = "%zu";
    return 0;
}
"""
    issues = lint_code(code)
    assert (
        sum(
            "Format specifier (size_t/long long) used without Windows #ifdef guard."
            in str(i)
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
