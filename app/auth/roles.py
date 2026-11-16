"""Who is a librarian.

Anyone signed in may read the library and upload a BOM. Changing what the
library holds, or throwing a record away, is a librarian's to do.

Librarians are named in the app's own settings rather than by a claim from the
identity provider. KiCad keeps its tokens in the OS credential store, which on
Windows has room for about 1280 characters, and the tokens already use most of
that. A role claim would be carried in two of the three tokens; a list here
costs nothing.
"""

from __future__ import annotations

from collections.abc import Collection

from app.auth.identity import Principal
from app.domain.policy import Actor

# The claims a librarian may be listed by. Not `name`: that is what a person
# chooses to be called, and two people can choose the same thing.
_IDENTIFYING_CLAIMS = ("preferred_username", "email")


def is_librarian(
    principal: Principal | None, librarians: Collection[str], *, auth_configured: bool
) -> bool:
    """Whether this person may do what only a librarian may.

    With sign-in switched off there is nobody to tell apart, and the library
    is by declaration on a network where everyone is trusted -- so everyone is
    a librarian. Refusing would only make the features unusable there.
    """
    if not auth_configured:
        return True
    if principal is None:
        return False

    listed = {name.strip().lower() for name in librarians}
    known_as = {principal.subject.lower()}
    known_as.update(
        str(principal.claims[claim]).lower()
        for claim in _IDENTIFYING_CLAIMS
        if principal.claims.get(claim)
    )
    return not listed.isdisjoint(known_as)


def actor_for(
    principal: Principal | None,
    librarians: Collection[str],
    *,
    auth_configured: bool,
    hostname: str,
) -> Actor:
    """Whoever is asking, as the rules about who may change what see them.

    `hostname` is this deployment's, for the address given to someone the
    identity provider gave none.
    """
    return Actor(
        librarian=is_librarian(principal, librarians, auth_configured=auth_configured),
        name=principal.display_name if principal else "",
        email=principal.address(hostname) if principal else f"nobody@users.noreply.{hostname}",
    )
