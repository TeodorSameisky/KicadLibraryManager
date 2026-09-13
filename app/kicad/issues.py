"""Problems found while reading a library.

Their own module because everything that inspects a library produces them --
the asset indexer, the parts loader, the catalog -- and none of those should
have to import another just to report a problem.

An issue is data, never an exception. A real library always has some, and the
useful output is a list rather than a stack trace from the first one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    kind: str
    message: str
    path: str | None = None

    def __str__(self) -> str:
        where = f" [{self.path}]" if self.path else ""
        return f"{self.severity.value}: {self.message}{where}"

    @property
    def is_error(self) -> bool:
        return self.severity is Severity.ERROR

    def as_dict(self) -> dict[str, str | None]:
        return {
            "severity": self.severity.value,
            "kind": self.kind,
            "message": self.message,
            "path": self.path,
        }


def error(kind: str, message: str, path: str | None = None) -> Issue:
    return Issue(Severity.ERROR, kind, message, path)


def warning(kind: str, message: str, path: str | None = None) -> Issue:
    return Issue(Severity.WARNING, kind, message, path)


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity is Severity.ERROR]


def warnings(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity is Severity.WARNING]
