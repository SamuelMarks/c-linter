from unittest.mock import MagicMock, patch
from clang.cindex import CursorKind, TypeKind

from c_linter.linter import (
    _extract_clang_args,
    _analyze_ast,
    _get_nolint_lines,
    lint_file,
    lint_code,
)


def test_extract_clang_args_boundary():
    # Branch 38->50
    assert _extract_clang_args(["clang", "-I"]) == ["-I"]


class BadResultType:
    @property
    def kind(self):
        raise ValueError("mock error")


def test_analyze_ast_ret_type_none():
    # Branch 354->403
    mock_cursor = MagicMock()
    mock_cursor.location.file = None
    mock_cursor.kind = CursorKind.FUNCTION_DECL
    mock_cursor.is_definition.return_value = True

    mock_cursor.result_type = BadResultType()

    issues = []
    _analyze_ast(mock_cursor, "test.c", issues, False, False, set(), [], source_code="")
    assert len(issues) == 0


def test_analyze_ast_out_of_bounds_source():
    # Branches 454->465 and 457->465
    mock_cursor = MagicMock()
    mock_cursor.location.file = None  # prevent early exit
    mock_cursor.kind = CursorKind.CALL_EXPR
    mock_cursor.type.get_canonical.return_value.kind = TypeKind.INT
    mock_cursor.referenced = None
    mock_cursor.spelling = "some_func"
    mock_cursor.extent.start.line = 5  # > len(lines)
    mock_cursor.extent.start.column = 5

    # 454->465: line index out of bounds
    issues = []
    _analyze_ast(
        mock_cursor,
        "test.c",
        issues,
        False,
        False,
        set(),
        [],
        source_code="line1\nline2\n",
    )
    # issue should be added because it's unassigned
    assert len(issues) == 0  # no parent to check if unassigned, wait

    # Let's provide a parent so it actually gets added to issues
    mock_parent = MagicMock()
    mock_parent.kind = CursorKind.COMPOUND_STMT
    issues = []
    _analyze_ast(
        mock_cursor,
        "test.c",
        issues,
        False,
        False,
        set(),
        [],
        source_code="line1\nline2\n",
        parent=mock_parent,
    )
    assert len(issues) == 1

    # 457->465: column index out of bounds
    mock_cursor.extent.start.line = 1
    mock_cursor.extent.start.column = 20  # > len("line1")
    issues = []
    _analyze_ast(
        mock_cursor,
        "test.c",
        issues,
        False,
        False,
        set(),
        [],
        source_code="line1\nline2\n",
        parent=mock_parent,
    )
    assert len(issues) == 1


def test_analyze_ast_strict_safe_crt_false():
    # Branch 488->500
    mock_cursor = MagicMock()
    mock_cursor.location.file = None
    mock_cursor.kind = CursorKind.CALL_EXPR
    mock_cursor.spelling = "strncpy"  # an unsafe func that requires strict_safe_crt=True to complain if check_safe_crt=True
    # ret type to avoid int discarded return logic error
    mock_cursor.type.get_canonical.return_value.kind = TypeKind.POINTER

    issues = []
    _analyze_ast(mock_cursor, "test.c", issues, True, False, set(), [], source_code="")
    assert len(issues) == 0


def test_nolint_multiple_on_same_line():
    # Branches 679->681, 687->689, 692->694
    # 679->681: line_num already in ignored_lines from previous regex match
    code = "// NOLINT(a) NOLINT(b)\n"
    # 687->689: target_line already in ignored_lines
    code += "// NOLINTNEXTLINE(c) NOLINTNEXTLINE(d)\n"
    # 692->694: line_num already in ignored_lines from specific NOLINT(x) and then general NOLINT
    code += "// NOLINT(e) NOLINT\n"

    ignored, _ = _get_nolint_lines(code)
    assert ignored[1] == {"a", "b"}
    assert ignored[3] == {"*", "c", "d", "e"}


@patch("c_linter.linter.Index")
def test_lint_file_no_test_relaxations(mock_index):
    # Branch 793->805
    mock_tu = MagicMock()
    mock_tu.diagnostics = []
    mock_index.create.return_value.parse.return_value = mock_tu

    lint_file("test.c", no_test_relaxations=True)


@patch("c_linter.linter.CompilationDatabase")
@patch("c_linter.linter.Index")
def test_lint_file_empty_cmds(mock_index, mock_compdb):
    # Branch 852->858
    mock_db = MagicMock()
    mock_db.getCompileCommands.return_value = []
    mock_compdb.fromDirectory.return_value = mock_db

    mock_tu = MagicMock()
    mock_tu.diagnostics = []
    mock_index.create.return_value.parse.return_value = mock_tu

    lint_file("test.c", build_dir="some_dir")


@patch("c_linter.linter.Index")
def test_lint_code_branches(mock_index):
    # Branch 956->968 (no_test_relaxations), 984->986 (no_pedantic)
    mock_tu = MagicMock()
    mock_tu.diagnostics = []
    mock_index.create.return_value.parse.return_value = mock_tu

    lint_code("int main() { return 0; }", no_test_relaxations=True, no_pedantic=True)


def test_allocations_walk_branches():
    # Branches 564->572, 566->572, 601->605, 626->630
    # Provide actual C code that triggers these.

    # 564->572: parent not VAR_DECL or BINARY_OPERATOR (e.g., RETURN_STMT)
    # 566->572: BINARY_OPERATOR but first child not DECL_REF_EXPR (e.g., *ptr = malloc(10))
    # 601->605: UNARY_OPERATOR that is not '!' or '*' (e.g., bitwise NOT ~)
    code = """
    #include <stdlib.h>
    int* func1() {
        return malloc(10); // 564->572
    }
    void func2() {
        int *ptr;
        int **pptr = &ptr;
        *pptr = malloc(10); // 566->572
        
        int x = 5;
        if (~x) {} // 601->605 (unary operator ~)
    }
    """
    issues = lint_code(code)
    # Just running this code is enough if the clang parser hits those branches during AST traversal.
    # To hit 626->630: IF_STMT with no children? The clang parser won't generate an IF_STMT without children.
    # So we must mock it for 626->630.

    from c_linter.linter import _check_allocations

    mock_tu_cursor = MagicMock()
    mock_func_decl = MagicMock()
    mock_func_decl.kind = CursorKind.FUNCTION_DECL
    mock_func_decl.is_definition.return_value = True
    mock_func_decl.location.file.name = "test.c"

    mock_if_stmt = MagicMock()
    mock_if_stmt.kind = CursorKind.IF_STMT
    mock_if_stmt.get_children.return_value = []  # no children!

    mock_func_decl.get_children.return_value = [mock_if_stmt]
    mock_tu_cursor.get_children.return_value = [mock_func_decl]

    issues = []
    _check_allocations(mock_tu_cursor, "test.c", issues)
    assert len(issues) == 0


@patch("c_linter.linter.Index")
def test_lint_file_ignore_returns_provided(mock_index):
    # Branch 789->792
    mock_tu = MagicMock()
    mock_tu.diagnostics = []
    mock_index.create.return_value.parse.return_value = mock_tu

    lint_file("test.c", ignore_returns=["foo"])
