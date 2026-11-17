"""Adding symbols, footprints and 3D models to the library, and taking them out.

Anyone may add what the library does not have: a part cannot be requested
without a symbol, and waiting on a librarian for one would send the request
somewhere else. Replacing what is there changes every part drawn with it, and
removing it is gone for good, so those are a librarian's.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.domain import policy
from app.domain.policy import Actor
from app.domain.refs import LibraryRef
from app.errors import NotAllowed, NotFound
from app.library import previews, uploads
from app.library.assets import MODEL_DIR_SUFFIX
from app.library.editing import EditError
from app.library.refs import model_key
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.library.uploads import AssetKind, Plan, UploadedFile
from app.services import changes


def check_upload(
    snapshot: Snapshot, library: str, files: Sequence[UploadedFile], actor: Actor
) -> Plan:
    """What an upload would do, and what stands in its way. Changes nothing."""
    plan = uploads.plan_upload(library, files, snapshot)
    if plan.replaces and not policy.may_replace_assets(actor):
        replaced = [item.ref(plan.library) for item in plan.items if item.replaces]
        plan.problems.append(
            f"The library already has {', '.join(replaced)}. Only a librarian replaces "
            "what is there; upload under another name to add it alongside."
        )
    return plan


async def upload(
    library: LibraryService,
    name: str,
    files: Sequence[UploadedFile],
    *,
    replace: bool = False,
    note: str = "",
    actor: Actor,
) -> Plan:
    """Add what was uploaded to the library, as one commit in the actor's name.

    What would replace something already there is refused unless `replace`
    says that is meant: the page shows what would be replaced before asking.
    """
    changes.require_writable(library)
    plan = check_upload(library.snapshot, name, files, actor)
    if plan.problems:
        raise EditError(plan.problems)
    if plan.replaces and not replace:
        replaced = [item.ref(plan.library) for item in plan.items if item.replaces]
        raise EditError([f"This would replace {', '.join(replaced)}. Say that it should."])

    await changes.publish(library, lambda root: uploads.write_upload(root, plan), actor, note=note)
    return plan


async def remove(
    library: LibraryService, kind: AssetKind, ref: str, *, note: str = "", actor: Actor
) -> None:
    """Take a symbol, footprint or 3D model that nothing uses out of the library."""
    changes.require_writable(library)
    if not policy.may_replace_assets(actor):
        raise NotAllowed("Only a librarian can do this.")
    removal, problems = uploads.plan_removal(kind, ref, library.snapshot)
    if removal is None:
        raise EditError(problems)
    await changes.publish(
        library, lambda root: uploads.write_removal(root, removal), actor, note=note
    )


# -- what the library has ---------------------------------------------------------

# As many rows as a page lists before asking for the search to be narrowed:
# KiCad's own libraries hold tens of thousands of footprints.
LIST_LIMIT = 300


@dataclass(frozen=True)
class AssetRow:
    kind: AssetKind
    library: str
    name: str
    ref: str  # Passives:R_0603, or Passives.3dshapes/R_0603.step for a model
    source: str
    path: str
    # Parts by IPN for a symbol or footprint; footprints for a model.
    used_by: tuple[str, ...]


@dataclass(frozen=True)
class AssetListing:
    rows: list[AssetRow]
    total: int  # matching, before the limit


def _users(snapshot: Snapshot, kind: AssetKind) -> dict[str, list[str]]:
    users: dict[str, list[str]] = defaultdict(list)
    if kind is AssetKind.MODEL:
        for index in snapshot.catalog.indexes.values():
            for footprint in index.footprints.values():
                for reference in footprint.model_refs:
                    users[model_key(reference)].append(f"{footprint.library}:{footprint.name}")
    else:
        for part in snapshot.parts.parts.values():
            ref = part.symbol if kind is AssetKind.SYMBOL else part.footprint
            if ref:
                users[ref].append(part.ipn)
    return users


def list_assets(
    snapshot: Snapshot, kind: AssetKind, query: str = "", limit: int = LIST_LIMIT
) -> AssetListing:
    """The library's symbols, footprints or models, each with what uses it.

    `query` narrows by words, every one of which has to be in the name, the
    library or the source.
    """
    words = query.casefold().split()
    users = _users(snapshot, kind)
    rows: list[AssetRow] = []
    for source, index in snapshot.catalog.indexes.items():
        found: list[tuple[str, str, Path]]
        if kind is AssetKind.SYMBOL:
            found = [(s.library, s.name, s.path) for s in index.symbols.values()]
        elif kind is AssetKind.FOOTPRINT:
            found = [(f.library, f.name, f.path) for f in index.footprints.values()]
        else:
            found = [(m.library, m.name, m.path) for m in index.models.values()]
        for library_name, name, path in found:
            ref = (
                f"{library_name}{MODEL_DIR_SUFFIX}/{name}"
                if kind is AssetKind.MODEL
                else f"{library_name}:{name}"
            )
            text = f"{ref} {source}".casefold()
            if all(word in text for word in words):
                rows.append(
                    AssetRow(
                        kind=kind,
                        library=library_name,
                        name=name,
                        ref=ref,
                        source=source,
                        path=index.relative(path),
                        used_by=tuple(sorted(users.get(ref, ()))),
                    )
                )
    rows.sort(key=lambda row: (row.ref.casefold(), row.source))
    return AssetListing(rows=rows[:limit], total=len(rows))


@dataclass(frozen=True)
class AssetDetail:
    row: AssetRow
    drawing: str | None
    # For a footprint, the models it names and whether the library has each.
    models: tuple[tuple[str, bool], ...] = ()
    # For a symbol, the symbols that derive from it.
    derived: tuple[str, ...] = ()
    # Whether it is in the repository this app writes to.
    own: bool = False


def asset_detail(snapshot: Snapshot, kind: AssetKind, ref: str) -> AssetDetail:
    """One symbol, footprint or model: drawn, with what uses it."""
    owner = uploads.own_index(snapshot)
    own_source = owner[0] if owner else None
    listing = [row for row in list_assets(snapshot, kind, limit=10**9).rows if row.ref == ref]
    if not listing:
        raise NotFound(f"The library has no {kind.label} {ref}.")
    # Where two sources have it, the first configured is the one used.
    order = list(snapshot.catalog.indexes)
    row = min(listing, key=lambda each: order.index(each.source))
    index = snapshot.catalog.indexes[row.source]

    if kind is AssetKind.SYMBOL:
        parsed = LibraryRef(row.library, row.name)
        return AssetDetail(
            row=row,
            drawing=previews.library_symbol_svg(snapshot, parsed),
            derived=tuple(uploads.symbols_extending(index, row.library, row.name)),
            own=row.source == own_source,
        )
    if kind is AssetKind.FOOTPRINT:
        footprint = index.footprints[(row.library, row.name)]
        return AssetDetail(
            row=row,
            drawing=previews.library_footprint_svg(footprint),
            models=tuple(
                (reference, snapshot.catalog.model(model_key(reference)) is not None)
                for reference in footprint.model_refs
            ),
            own=row.source == own_source,
        )
    return AssetDetail(row=row, drawing=None, own=row.source == own_source)


def require_kind(kind: str) -> AssetKind:
    if kind not in AssetKind:
        raise NotFound("There is no such kind of library file.")
    return AssetKind(kind)
