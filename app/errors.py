"""What can go wrong that whoever asked can be told about.

A handful of kinds, and every error the app raises on purpose is one of them.
The kind is what the caller needs in order to know what to do next: ask for
something else, sign in as someone else, put the request right, try later.
What that means over HTTP is decided once, in `app.web.errors`, and nowhere
else: nothing below the web layer knows a status code.

A new error is a subclass of the kind it is. It then reaches its caller in
the right shape without a route having to catch it.
"""

from __future__ import annotations

from collections.abc import Sequence


class AppError(Exception):
    """Something that was asked for cannot be done."""

    def detail(self) -> str:
        """What to tell whoever asked. The error's own message, unless that
        says something meant for the log -- a path on the server -- in which
        case a subclass says something else here."""
        return str(self)


class NotFound(AppError):
    """There is no such thing."""


class NotAllowed(AppError):
    """There is, but it is not this person's to do."""


class Invalid(AppError):
    """What was asked for does not make sense as asked."""

    # Everything that is wrong, one sentence each, for an error that found
    # more than one thing. Empty for one that is a single sentence.
    problems: Sequence[str] = ()


class Conflict(AppError):
    """It made sense, but not with things as they stand."""


class Unavailable(AppError):
    """Not possible right now, through no fault of the request."""


class UpstreamFailed(AppError):
    """Something this app depends on -- the git host -- refused or failed."""
