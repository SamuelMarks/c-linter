"""C Linter SDK.

A robust Python SDK for linting C code to ensure adherence to strict standards,
such as C89 compliance, proper allocation checking, strictly defined return types,
and nodiscard integer return enforcement.

Example:
    >>> from c_linter import lint_code, lint_file
    >>> issues = lint_code("int main() { return 0; }")
    >>> for issue in issues:
    ...     print(issue)

"""

from .linter import lint_code, lint_file
from .models import Issue

__all__ = ["lint_code", "lint_file", "Issue"]
