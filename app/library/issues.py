"""Problems found while reading a library.

An issue is data, never an exception. A real library always has some, and the
useful output is the whole list rather than a stack trace from the first.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    kind: str  # a stable slug, so the same mistake can be grouped and counted
    message: str
    # Relative to the repository it is in: what a maintainer opens to fix it,
    # and nothing about where the server keeps its clones.
    path: str | None = None
    source: str | None = None

    @staticmethod
    def error(kind: str, message: str, path: str | None = None) -> Issue:
        return Issue(Severity.ERROR, kind, message, path)

    @staticmethod
    def warning(kind: str, message: str, path: str | None = None) -> Issue:
        return Issue(Severity.WARNING, kind, message, path)

    def from_source(self, source: str) -> Issue:
        return replace(self, source=source)

    def __str__(self) -> str:
        where = "/".join(part for part in (self.source, self.path) if part)
        return f"{self.severity.value}: {self.message}" + (f" [{where}]" if where else "")


def errors(issues: Iterable[Issue]) -> list[Issue]:
    return [issue for issue in issues if issue.severity is Severity.ERROR]


def warnings(issues: Iterable[Issue]) -> list[Issue]:
    return [issue for issue in issues if issue.severity is Severity.WARNING]
