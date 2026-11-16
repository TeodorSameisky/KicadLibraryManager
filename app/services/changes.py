"""What every change to the library's repository has in common.

Each one ends in a commit, made in the name of whoever asked for it.
"""

from __future__ import annotations

import re

from app.domain.policy import Actor
from app.errors import Unavailable
from app.library.publish import Identity
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.library.writer import Write


class NotWritable(Unavailable):
    """This deployment reads the library and has no way to change it."""

    def __init__(self) -> None:
        super().__init__("This deployment is not set up to change the library.")


def require_writable(library: LibraryService) -> None:
    """Asked before anything else: with no way to save a change, there is
    nothing to be gained from saying what else is wrong with it."""
    if not library.writable:
        raise NotWritable


def author_of(actor: Actor) -> Identity:
    """Who a commit is made in the name of."""
    # A name goes into a commit header; keep what would break one out of it.
    name = re.sub(r"[<>\r\n]", "", actor.name).strip() or "Someone"
    return Identity(name, actor.email)


async def publish(library: LibraryService, write: Write, actor: Actor) -> Snapshot:
    """Make a change, commit it in the actor's name and push it.

    Returns the library as indexed after the change. Raises `PublishError` if
    the repository would not take it.
    """
    require_writable(library)
    return await library.change(write, author_of(actor))
