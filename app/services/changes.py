"""What every change to the library's repository has in common.

Each one ends in a commit, made in the name of whoever asked for it, with
what they said about it under the subject the app wrote.
"""

from __future__ import annotations

import re

from app.domain.policy import Actor
from app.errors import Unavailable
from app.library.publish import Identity
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.library.writer import Write

# As long as a note may be. What someone says about a change is a few
# sentences, not a document.
MAX_NOTE = 1000


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


def with_note(subject: str, note: str = "") -> str:
    """A commit message: the subject the app wrote, then what the person said.

    The note is theirs and goes in as written, except that what could not be
    printed is dropped and the lines are tidied. It is the body of the message,
    where git shows it under the subject and the history page shows it under
    the change.
    """
    lines = note.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    text = "\n".join("".join(c for c in line if c.isprintable()).rstrip() for line in lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return f"{subject}\n\n{text}" if text else subject


async def publish(
    library: LibraryService, write: Write, actor: Actor, *, note: str = ""
) -> Snapshot:
    """Make a change, commit it in the actor's name and push it.

    `write` changes the files and returns the commit's subject; `note` is
    what the person said about the change, and goes under it. Returns the
    library as indexed after the change. Raises `PublishError` if the
    repository would not take it.
    """
    require_writable(library)
    return await library.change(lambda root: with_note(write(root), note), author_of(actor))
