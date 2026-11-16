"""Requesting a part, correcting it, and approving it.

Anyone may request a part: it is added as a draft under the next number in
its category. A draft may be corrected by anyone. Once a librarian has
approved it, only a librarian changes it.

Each is the same three steps: who may do it, whether what was asked makes
sense against the library as it stands (`check`), and the change itself,
which ends in a commit to the library's repository made in the name of
whoever asked.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app.domain import policy, rules
from app.domain.forms import PartForm
from app.domain.parts import Lifecycle, Part, is_web_address
from app.domain.policy import Actor
from app.domain.rules import Rule
from app.errors import NotAllowed
from app.library import editing
from app.library.editing import EditError
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.services import changes

MAX_FIELDS = 40
MAX_SOURCES = 20


def require_may_edit(actor: Actor, part: Part) -> None:
    if not policy.may_edit(actor, part):
        raise NotAllowed("Only a librarian can change a part that is not a draft.")


# -- whether a request makes sense --------------------------------------------

# What a part falls short of, as the person asking for it is told. The same
# rules the library's own parts are held to, where they are listed as issues;
# here they are reasons to refuse, said to someone who can put them right.
_SENTENCES: dict[Rule, str] = {
    Rule.NO_DESCRIPTION: "Give the part a description; the number alone cannot be searched for.",
    Rule.NO_SYMBOL: "Choose a symbol.",
    Rule.UNQUALIFIED_SYMBOL: "Choose a symbol.",
    Rule.MISSING_SYMBOL: "The library has no symbol {symbol}.",
    Rule.UNPLACEABLE: "The symbol {symbol} cannot be placed: {detail}.",
    Rule.UNQUALIFIED_FOOTPRINT: "The library has no footprint {footprint}.",
    Rule.MISSING_FOOTPRINT: "The library has no footprint {footprint}.",
    Rule.TEMPLATE: "{detail}",
    Rule.MULTIPLE_PREFERRED: "Only one manufacturer part can be the preferred one.",
}


def check(form: PartForm, snapshot: Snapshot, *, new: bool, category: str | None = None) -> None:
    """Raise EditError with everything that is wrong, not just the first.

    `category` is the category of the part being edited. A new part says its
    own.
    """
    problems: list[str] = []
    category = form.category if new else category

    if new and form.category not in snapshot.parts.categories:
        problems.append("Choose a category.")

    # What is asked of any part, whether it is being requested or is already
    # in the library.
    for finding in rules.part_findings(
        description=form.description,
        symbol=form.symbol,
        footprint=form.footprint,
        fields=form.fields,
        preferred=[entry.preferred for entry in form.sources],
        template=snapshot.parts.templates.get(category or ""),
        assets=snapshot.catalog,
    ):
        problems.append(
            _SENTENCES[finding.rule].format(
                symbol=form.symbol, footprint=form.footprint, detail=finding.detail
            )
        )

    # And what is asked of a request as such: that it can be written down.
    if len(form.fields) > MAX_FIELDS:
        problems.append(f"A part can carry at most {MAX_FIELDS} fields.")
    if any(not name.strip() for name in form.fields):
        problems.append("Every field needs a name.")

    if len(form.sources) > MAX_SOURCES:
        problems.append(f"A part can list at most {MAX_SOURCES} manufacturer parts.")
    names = [entry.mpn for entry in form.sources]
    if any(not name.strip() for name in names):
        problems.append("Every manufacturer part needs its part number.")
    if len(set(names)) != len(names):
        problems.append("A manufacturer part is listed twice.")

    for entry in form.sources:
        if entry.mpn in snapshot.parts.mpns:
            continue  # already in the library; what the form says about it is not used
        if entry.lifecycle not in Lifecycle:
            problems.append(f"{entry.mpn}: the lifecycle must be one of {', '.join(Lifecycle)}.")
        if entry.datasheet and not is_web_address(entry.datasheet):
            problems.append(f"{entry.mpn}: the datasheet must be a web address.")

    if problems:
        raise EditError(problems)


def _under_one_name(form: PartForm, snapshot: Snapshot) -> PartForm:
    """The form, with each manufacturer under the name the library has for it.

    A new manufacturer part is written down as "Texas Instruments" whether
    "TI" or "Texas Instruments Inc." was typed. A name the library does not
    know is kept as typed: the part is still a draft someone can work on, and
    the issues page says which name needs a place in manufacturers.yaml.
    """
    known = snapshot.parts.manufacturers
    return replace(
        form,
        sources=tuple(
            replace(entry, manufacturer=known.canonical(entry.manufacturer))
            for entry in form.sources
        ),
    )


# -- the changes ----------------------------------------------------------------


async def request_part(
    library: LibraryService, form: PartForm, *, note: str = "", actor: Actor
) -> str:
    """Add a part as a draft, under the next number in its category.

    Returns the number it was given.
    """
    changes.require_writable(library)
    check(form, library.snapshot, new=True)
    form = _under_one_name(form, library.snapshot)

    given: dict[str, str] = {}

    def write(root: Path) -> str:
        # The number is taken here, from the files as they are once the
        # repository has been brought up to date, and not before.
        given["ipn"], subject = editing.write_new_part(root, form)
        return subject

    await changes.publish(library, write, actor, note=note)
    return given["ipn"]


async def edit_part(
    library: LibraryService, part: Part, form: PartForm, *, note: str = "", actor: Actor
) -> None:
    """Change a part, keeping its number and its status."""
    changes.require_writable(library)
    require_may_edit(actor, part)
    check(form, library.snapshot, new=False, category=part.category)
    form = _under_one_name(form, library.snapshot)

    await changes.publish(
        library, lambda root: editing.write_part(root, part.ipn, form), actor, note=note
    )


async def set_status(
    library: LibraryService, part: Part, status: str, *, note: str = "", actor: Actor
) -> None:
    """Approve a part, retire it, or send it back to being a draft."""
    changes.require_writable(library)
    if not policy.may_decide(actor):
        raise NotAllowed("Only a librarian can do this.")

    await changes.publish(
        library, lambda root: editing.write_status(root, part.ipn, status), actor, note=note
    )
