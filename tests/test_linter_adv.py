from c_linter import lint_code, lint_file
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
    # The first fopen is on line 3, should be suppressed
    assert len([i for i in issues if "fopen" in i.message]) == 2

def test_nolint_nextline():
    code = """
int main(void) {
    // NOLINTNEXTLINE
    fopen("test", "r");
    return 0;
}
"""
    issues = lint_code(code)
    assert not issues

def test_tolerate_c99_extensions():
    code = """
_Bool b = 1;
long long a = 0;
"""
    # by default tolerate_c99_types is True
    issues = lint_code(code, std="c89")
    assert not any("C99 extension" in i.message or "long long" in i.message for i in issues)
    
    # if False
    issues = lint_code(code, std="c89", tolerate_c99_types=False)
    assert any("extension" in i.message for i in issues)

def test_header_strategy(tmp_path):
    p = tmp_path / "test.h"
    p.write_text("bool a = true;\n")
    # should inject stdbool.h
    issues = lint_file(str(p))
    assert not any("unknown type name 'bool'" in i.message for i in issues)
    
    # disable strategy
    issues = lint_file(str(p), header_only_strategy=False)
    assert any("unknown type name 'bool'" in i.message for i in issues)

