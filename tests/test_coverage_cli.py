import os
from unittest.mock import patch
import pytest

from c_linter.cli import _find_compile_commands, _gather_include_dirs, main
from c_linter.models import Issue

def test_find_compile_commands_loop_exhaustion(tmp_path):
    # Branch 46->44: loop terminates when parent == base
    # We pass a root-like directory structure or just something that exhausts the while loop without finding compile_commands.json
    assert _find_compile_commands([str(tmp_path)]) == ""

def test_gather_include_dirs_exclude_file_parent(tmp_path):
    # Branch 110->102: if not _match_exclude(dir_path, exclude_patterns): condition is False
    # This means we found a .h file, but its directory is excluded
    h_file = tmp_path / "test.h"
    h_file.touch()
    
    # Exclude the directory of tmp_path
    res = _gather_include_dirs([str(h_file)], exclude_patterns=[f"*{tmp_path.name}*"])
    assert res == []

@patch("c_linter.cli._find_compile_commands")
@patch("c_linter.cli.lint_file")
@patch("sys.argv", ["c-linter", "test.c", "--build-dir", "some_dir"])
def test_main_build_dir_provided(mock_lint, mock_find, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test.c").touch()
    # Branch 284->287: if not build_dir is False
    mock_lint.return_value = []
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 0
    mock_find.assert_not_called()

@patch("c_linter.cli.lint_file")
def test_main_include_already_present(mock_lint, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test.c").touch()
    # Branch 292->291: if ai not in includes is False
    # We'll create an include dir with a header so it gets gathered
    inc_dir = tmp_path / "include"
    inc_dir.mkdir()
    (inc_dir / "test.h").touch()
    
    with patch("sys.argv", ["c-linter", "test.c", "-I", str(inc_dir)]):
        with patch("c_linter.cli._gather_include_dirs", return_value=[str(inc_dir)]):
            mock_lint.return_value = []
            with pytest.raises(SystemExit) as e:
                main()
            assert e.value.code == 0

@patch("c_linter.cli.lint_file")
def test_main_fixed_issue(mock_lint, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test.c").touch()
    # Branch 336->334: if not getattr(issue, "fixed", False) is False
    issue = Issue(file="test.c", line=1, column=1, message="test", fixed=True)
    mock_lint.return_value = [issue]
    
    with patch("sys.argv", ["c-linter", "test.c", "--fix"]):
        with pytest.raises(SystemExit) as e:
            main()
        # It should pass with code 0 because the issue was fixed
        assert e.value.code == 0
