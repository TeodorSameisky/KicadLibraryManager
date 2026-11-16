"""Who may change what.

Anyone who can reach the library may read it, upload a BOM, request a part,
and correct a draft. What decides something, or throws a record away, is a
librarian's to do: approving a part, changing or retiring an approved one,
editing a category's template, refreshing the index, deleting a BOM, taking a
copy of the database.

The rules are here so that a route, a page and a script are all told the same
thing. Who counts as a librarian is not decided here: that depends on how
people sign in, and is `app.auth.roles`' to say.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.parts import Part, PartStatus


@dataclass(frozen=True)
class Actor:
    """Whoever is asking: what they may do, and what their changes are
    recorded under."""

    librarian: bool = False
    # As they are known, for the record of who did something. Empty when
    # nobody is signed in, which is only possible with sign-in switched off.
    name: str = ""
    # An address to record their changes under. Their own if the identity
    # provider gave one, otherwise one that names them and receives nothing.
    email: str = ""


def may_edit(actor: Actor, part: Part) -> bool:
    """A draft is still being worked out, by whoever is working it out.
    What a librarian has approved is the librarians' to change."""
    return part.status == PartStatus.DRAFT or actor.librarian


def may_decide(actor: Actor) -> bool:
    """Approving a part, retiring it, or sending it back to being a draft."""
    return actor.librarian


def may_edit_templates(actor: Actor) -> bool:
    """A template decides what every part in a category must say."""
    return actor.librarian


def may_delete_boms(actor: Actor) -> bool:
    """A BOM is a record of what was built, and deleting one cannot be undone."""
    return actor.librarian
