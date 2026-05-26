"""Models for the C Linter SDK."""

from dataclasses import dataclass


@dataclass
class Issue:
    """Represents a linting issue found in the C codebase.

    Attributes:
        file (str): The name or path of the file where the issue occurred.
        line (int): The line number where the issue was found (1-indexed).
        column (int): The column number where the issue was found (1-indexed).
        message (str): A descriptive message explaining the linting rule violation.
    """

    file: str
    line: int
    column: int
    message: str

    def __str__(self) -> str:
        """Format the issue as a standard compiler-like diagnostic string.

        Returns:
            str: The formatted issue string in the form "file:line:column: message".
        """
        return f"{self.file}:{self.line}:{self.column}: {self.message}"
