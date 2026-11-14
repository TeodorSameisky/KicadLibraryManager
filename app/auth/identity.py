"""An authenticated user."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# The claims that can name a person, most recognisable first.
NAME_CLAIMS = ("name", "preferred_username", "email")


@dataclass(frozen=True)
class Principal:
    subject: str
    claims: Mapping[str, Any] = field(default_factory=dict)

    @property
    def has_name(self) -> bool:
        return any(self.claims.get(claim) for claim in NAME_CLAIMS)

    @property
    def display_name(self) -> str:
        """Something a person would recognise as themselves.

        Scopes are kept minimal so that KiCad can store its tokens, which
        means there is often no name claim at all and the subject is an
        opaque hash. Showing sixty-four characters of it helps nobody.
        """
        for claim in NAME_CLAIMS:
            value = self.claims.get(claim)
            if value:
                return str(value)
        return f"user {self.subject[:8]}" if len(self.subject) > 12 else self.subject
