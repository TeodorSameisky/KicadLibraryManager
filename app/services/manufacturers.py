"""Keeping the library's list of manufacturers.

The list says which name each manufacturer goes by and the other ways it gets
written. Keeping it is a librarian's to do, and each change is one commit to
the library's repository, in the name of whoever made it.

What the list is kept *for* is the names it does not have yet: every
manufacturer part that says who makes it in a way the list does not
recognise. `overview` is that worklist, beside the list itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain import policy
from app.domain.forms import ManufacturerForm
from app.domain.manufacturers import Manufacturer, name_key
from app.domain.policy import Actor
from app.errors import NotAllowed, NotFound
from app.library import manufacturer_editing as editing
from app.library.editing import EditError
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.services import changes

MAX_NAME = 120
MAX_ALIASES = 40


@dataclass(frozen=True)
class Listed:
    manufacturer: Manufacturer
    parts: int  # how many manufacturer parts it makes


@dataclass(frozen=True)
class Unlisted:
    """A name some manufacturer part gives that the list does not recognise."""

    spellings: tuple[str, ...]  # the ways it is written, the most used first
    parts: int  # how many manufacturer parts give it

    @property
    def name(self) -> str:
        return self.spellings[0]


@dataclass(frozen=True)
class Overview:
    listed: tuple[Listed, ...]  # by name
    unlisted: tuple[Unlisted, ...]  # the most used first


def overview(snapshot: Snapshot) -> Overview:
    """The list, with how much each manufacturer makes, and the names that
    are not on it."""
    known = snapshot.parts.manufacturers
    made: dict[str, int] = {}
    strangers: dict[str, dict[str, int]] = {}  # what a name comes to -> its spellings

    for mpn in snapshot.parts.mpns.values():
        if not mpn.manufacturer:
            continue
        found = known.resolve(mpn.manufacturer)
        if found is not None:
            made[found.name] = made.get(found.name, 0) + 1
        else:
            spellings = strangers.setdefault(name_key(mpn.manufacturer), {})
            spellings[mpn.manufacturer] = spellings.get(mpn.manufacturer, 0) + 1

    unlisted = [
        Unlisted(
            spellings=tuple(sorted(spellings, key=lambda s: (-spellings[s], s))),
            parts=sum(spellings.values()),
        )
        for spellings in strangers.values()
    ]
    return Overview(
        listed=tuple(
            Listed(manufacturer, made.get(manufacturer.name, 0))
            for manufacturer in sorted(known.known, key=lambda m: m.name.casefold())
        ),
        unlisted=tuple(sorted(unlisted, key=lambda u: (-u.parts, u.name.casefold()))),
    )


def find(snapshot: Snapshot, name: str) -> Manufacturer:
    """The manufacturer the list has under exactly this name."""
    for manufacturer in snapshot.parts.manufacturers.known:
        if manufacturer.name == name:
            return manufacturer
    raise NotFound(f"There is no manufacturer {name} in the list")


# -- whether a request makes sense --------------------------------------------


def _settled(form: ManufacturerForm, was: str | None) -> ManufacturerForm:
    """The form as it will be written down: tidied, each spelling once.

    A manufacturer that is renamed keeps its old name as another way of
    writing it. The files that say the old name are not rewritten, and
    without this every one of them would name nobody from then on.
    """
    name = form.name.strip()
    aliases: list[str] = []
    seen = {name_key(name)}
    for alias in (*form.aliases, *([was] if was else [])):
        spelling = alias.strip()
        # One that comes to the same as the name, or as one before it, adds
        # nothing: it is recognised already.
        if spelling and name_key(spelling) not in seen:
            seen.add(name_key(spelling))
            aliases.append(spelling)
    return ManufacturerForm(name=name, aliases=tuple(aliases))


def check_manufacturer(form: ManufacturerForm, snapshot: Snapshot, was: str | None = None) -> None:
    """Raise EditError with everything that is wrong, not just the first.

    `was` is the name of the manufacturer being changed; None for a new one.
    """
    problems: list[str] = []
    name = form.name.strip()

    if not name:
        problems.append("Give the manufacturer a name.")
    elif not name_key(name):
        problems.append("The name needs a letter or a digit in it.")
    if len(name) > MAX_NAME:
        problems.append(f"A name can be at most {MAX_NAME} characters.")
    if len(form.aliases) > MAX_ALIASES:
        problems.append(f"A manufacturer can have at most {MAX_ALIASES} other spellings.")

    # A spelling can mean one manufacturer only: anything looked up by it has
    # to come to one answer.
    taken = {
        name_key(spelling): other.name
        for other in snapshot.parts.manufacturers.known
        if other.name != was
        for spelling in other.spellings
    }
    owner = taken.get(name_key(name)) if name else None
    if owner == name:
        problems.append(f"{name} is already in the list.")
    elif owner is not None:
        problems.append(f"{name} is another way of writing {owner}, which is already listed.")

    for alias in dict.fromkeys(spelling.strip() for spelling in form.aliases):
        if len(alias) > MAX_NAME:
            problems.append(f"A spelling can be at most {MAX_NAME} characters.")
        owner = taken.get(name_key(alias)) if alias else None
        if owner is not None:
            problems.append(f"{alias} is already a way of writing {owner}.")

    if problems:
        raise EditError(problems)


# -- the changes ----------------------------------------------------------------


def _require_may_change(library: LibraryService, actor: Actor) -> None:
    if not policy.may_edit_manufacturers(actor):
        raise NotAllowed("Only a librarian can do this.")
    changes.require_writable(library)


async def save_manufacturer(
    library: LibraryService,
    form: ManufacturerForm,
    *,
    was: str | None = None,
    note: str = "",
    actor: Actor,
) -> Overview:
    """Add a manufacturer to the list, or change the one called `was`.

    Returns the list as it is afterwards, with the names still not on it.
    """
    _require_may_change(library, actor)
    snapshot = library.snapshot
    if was is not None:
        find(snapshot, was)
    check_manufacturer(form, snapshot, was)
    settled = _settled(form, was if was != form.name.strip() else None)

    after = await changes.publish(
        library,
        lambda root: editing.write_manufacturer(root, was, settled.name, settled.aliases),
        actor,
        note=note,
    )
    return overview(after)


async def remove_manufacturer(
    library: LibraryService, name: str, *, note: str = "", actor: Actor
) -> Overview:
    """Take a manufacturer off the list. The manufacturer parts that name it
    are not touched: they are then on the worklist, as naming nobody known."""
    _require_may_change(library, actor)
    find(library.snapshot, name)

    after = await changes.publish(
        library, lambda root: editing.delete_manufacturer(root, name), actor, note=note
    )
    return overview(after)
