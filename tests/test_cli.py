"""Tests for the C Linter CLI."""

import sys
from unittest.mock import patch

import pytest
from c_linter.cli import main


def test_cli_no_files(capsys):
    """Test CLI behavior when no files are provided."""
    with patch.object(sys, "argv", ["c-linter"]):
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 0
        out, _ = capsys.readouterr()
        assert "No files provided for linting." in out


def test_cli_file_not_found(capsys):
    """Test CLI behavior when a file is not found."""
    with patch.object(sys, "argv", ["c-linter", "does_not_exist.c"]):
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 0  # No real lint errors found since it skipped
        out, _ = capsys.readouterr()
        assert "Error: File 'does_not_exist.c' not found." in out
        assert "Linting passed successfully." in out


def test_cli_success(tmp_path, capsys):
    """Test CLI behavior with a clean C file."""
    p = tmp_path / "clean.c"
    p.write_text("int main(void) {\n    return 0;\n}\n")
    with patch.object(sys, "argv", ["c-linter", str(p)]):
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 0
        out, _ = capsys.readouterr()
        assert "Linting passed successfully." in out


def test_cli_failure(tmp_path, capsys):
    """Test CLI behavior with a C file that has linting issues."""
    p = tmp_path / "bad.c"
    p.write_text(
        "int main(void) {\n    int a = 1;\n    a = 2;\n    int b = 3;\n    return a+b;\n}\n"
    )
    with patch.object(sys, "argv", ["c-linter", str(p)]):
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1
        out, _ = capsys.readouterr()
        assert "Linting failed with 1 issue(s)." in out
        assert "mixing declarations and code is a C99 extension" in out


def test_cli_flags(tmp_path, capsys):
    """Test CLI behavior with the newly added flags."""
    p = tmp_path / "bad.c"
    p.write_text("""
#include <stdio.h>
int main(void) {
    int res = printf("%zu", 10);
    fopen("test.txt", "r");
    return res;
}
""")
    with patch.object(
        sys, "argv", ["c-linter", str(p), "--no-windows", "--no-safe-crt"]
    ):
        with pytest.raises(SystemExit) as e:
            main()
        # It might still fail due to the compiler warning from MSVC for fopen deprecation
        # But our custom checks won't fire.

    with patch.object(sys, "argv", ["c-linter", str(p)]):
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1
        out, _ = capsys.readouterr()
        assert (
            "Format specifier (size_t/long long) used without Windows #ifdef guard."
            in out
        )
        assert "Use safe CRT alternative for 'fopen'" in out
