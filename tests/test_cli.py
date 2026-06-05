"""Tests for the C Linter CLI."""

import os
import sys
from unittest.mock import patch, mock_open

import pytest
from c_linter.cli import main, _load_config, _find_compile_commands, _match_exclude, _gather_files


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
        assert e.value.code == 0
        out, _ = capsys.readouterr()
        assert "No files provided for linting." in out


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
    int res = printf("%I64d", 10);
    fopen("test.txt", "r");
    return res;
}
""")
    with patch.object(
        sys, "argv", ["c-linter", str(p), "--no-windows", "--no-safe-crt"]
    ):
        with pytest.raises(SystemExit) as e:
            main()

    with patch.object(sys, "argv", ["c-linter", str(p)]):
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1
        out, _ = capsys.readouterr()
        assert (
            "Format specifier (I64/I) used without Windows #ifdef guard."
            in out
        )
        assert "Use safe CRT alternative for 'fopen'" in out


def test_load_config_no_files():
    """Test load config with no configuration files present."""
    with patch("os.path.isfile", return_value=False):
        assert _load_config() == {}


def test_load_config_c_linter_toml():
    """Test load config from .c-linter.toml."""
    toml_content = b'std = "c99"\nexclude = ["build/"]\nignore_returns = ["printf"]\n'
    with patch("os.path.isfile", side_effect=lambda x: x == ".c-linter.toml"):
        with patch("builtins.open", mock_open(read_data=toml_content)):
            config = _load_config()
            assert config["std"] == "c99"
            assert config["exclude"] == ["build/"]
            assert config["ignore_returns"] == ["printf"]

    # Test load config exception
    with patch("os.path.isfile", side_effect=lambda x: x == ".c-linter.toml"):
        with patch("builtins.open", side_effect=Exception("Failed to read")):
            config = _load_config()
            assert config == {}


def test_load_config_pyproject_toml():
    """Test load config from pyproject.toml."""
    toml_content = b'[tool.c-linter]\nstd = "c11"\nno_safe_crt = true\n'
    with patch("os.path.isfile", side_effect=lambda x: x == "pyproject.toml"):
        with patch("builtins.open", mock_open(read_data=toml_content)):
            config = _load_config()
            assert config["std"] == "c11"
            assert config["no_safe_crt"] is True

    # Test load pyproject config exception
    with patch("os.path.isfile", side_effect=lambda x: x == "pyproject.toml"):
        with patch("builtins.open", side_effect=Exception("Failed to read")):
            config = _load_config()
            assert config == {}


def test_find_compile_commands(tmp_path):
    """Test finding compile_commands.json."""
    with patch("os.path.isfile", return_value=False):
        assert _find_compile_commands() == ""

def test_cli_exclude_pattern_asterisks():
    from c_linter.cli import _match_exclude
    assert _match_exclude("some/path/file.c", ["some/**"])
    assert _match_exclude("some/path/file.c", ["some**"])
    assert _match_exclude("some/path/file.c", ["path"])
    assert _match_exclude("some/path/file.c", ["*/path/*"])

    def mock_isfile(path):
        return path == os.path.join("build", "compile_commands.json")
    
    with patch("os.path.isfile", side_effect=mock_isfile):
        assert _find_compile_commands() == "build"


def test_match_exclude():
    """Test _match_exclude functionality."""
    assert _match_exclude("build/test.c", ["build/"]) is True
    assert _match_exclude("build/test.c", ["vendor/"]) is False
    assert _match_exclude("src/vendor/test.c", ["vendor/"]) is True
    assert _match_exclude("test.c", ["*.c"]) is True
    assert _match_exclude("src/test.c", ["src/*.c"]) is True
    assert _match_exclude("some/dir/test.c", ["dir"]) is True


def test_gather_files(tmp_path):
    """Test gathering files from directories while excluding some."""
    (tmp_path / "src").mkdir()
    (tmp_path / "build").mkdir()
    (tmp_path / "src" / "main.c").touch()
    (tmp_path / "src" / "header.h").touch()
    (tmp_path / "build" / "test.c").touch()
    (tmp_path / "README.md").touch()
    (tmp_path / "excluded_single.c").touch()
    
    # Exclude build dir
    files = _gather_files([str(tmp_path)], ["build/", "excluded_single.c"])
    assert len(files) == 2
    assert any("main.c" in f for f in files)
    assert any("header.h" in f for f in files)
    assert not any("test.c" in f for f in files)
    assert not any("README.md" in f for f in files)
    assert not any("excluded_single.c" in f for f in files)
    
    # Specific file inclusion
    files = _gather_files([str(tmp_path / "src" / "main.c")], [])
    assert len(files) == 1
    assert "main.c" in files[0]

    # Specific file exclusion
    files = _gather_files([str(tmp_path / "src" / "main.c")], ["main.c"])
    assert len(files) == 0

    # Non-existent file
    files = _gather_files([str(tmp_path / "does_not_exist.c")], [])
    assert len(files) == 0
