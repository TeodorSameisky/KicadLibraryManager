"""Keeping BOMs, and saying which device each is the BOM of.

These are plain functions, not coroutines: they talk to SQLite, which blocks,
so whoever calls them does it off the event loop.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.boms.parse import parse_bom
from app.boms.store import Bom, BomStore
from app.domain import policy
from app.domain.policy import Actor
from app.errors import Invalid, NotAllowed, NotFound
from app.library.snapshot import Snapshot


@dataclass(frozen=True)
class BomUpload:
    """A BOM exported from KiCad, as text, and what it is the BOM of."""

    project: str
    content: str
    revision: str = ""
    note: str = ""
    filename: str = ""
    # The device this is the BOM of, as "family/device" in the product tree;
    # empty when it is not for one of them.
    device: str = ""
    # Uploading a project's revision a second time replaces the first only
    # when this is set: overwriting a record should be chosen, not stumbled into.
    replace: bool = False


def get_bom(boms: BomStore, bom_id: int) -> Bom:
    bom = boms.get(bom_id)
    if bom is None:
        raise NotFound("There is no such BOM")
    return bom


def _device(key: str, snapshot: Snapshot) -> str:
    """A device key that is in the product tree, or "" for none."""
    key = key.strip()
    if key and snapshot.products.device(key) is None:
        raise Invalid(f"There is no device {key!r} in the products.")
    return key


def upload_bom(
    boms: BomStore, snapshot: Snapshot, upload: BomUpload, *, actor: Actor
) -> tuple[Bom, tuple[str, ...]]:
    """Read a BOM and keep it. One per project and revision.

    Returns the BOM as stored, and what the reader had to say about the file.
    Raises `BomError` if the text cannot be read as a BOM, and `BomExists` if
    one is already stored for this project and revision and it was not said
    to replace it.
    """
    device = _device(upload.device, snapshot)
    project, revision = " ".join(upload.project.split()), " ".join(upload.revision.split())
    if not project:
        raise Invalid("The project needs a name.")

    parsed = parse_bom(upload.content)
    bom = boms.save(
        parsed,
        project=project,
        revision=revision,
        filename=upload.filename,
        note=upload.note.strip(),
        uploaded_by=actor.name,
        source=upload.content,
        device=device,
        replace_existing=upload.replace,
    )
    return bom, parsed.warnings


def attach_bom(boms: BomStore, snapshot: Snapshot, bom: Bom, device: str) -> str:
    """Say which device a stored BOM is the BOM of, or take it off one.

    Returns the device it is now on, "" for none.
    """
    device = _device(device, snapshot)
    boms.attach(bom.id, device)
    return device


def delete_bom(boms: BomStore, bom_id: int, *, actor: Actor) -> None:
    """Throw a record away: a librarian's to do, since it cannot be undone."""
    if not policy.may_delete_boms(actor):
        raise NotAllowed("Only a librarian can do this.")
    boms.delete(get_bom(boms, bom_id).id)
