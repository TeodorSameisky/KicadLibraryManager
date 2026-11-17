"""Symbols, footprints and 3D models over the API: what the library has,
and adding to it."""

from __future__ import annotations

import base64
import binascii

from pydantic import BaseModel, Field

from app.library.editing import EditError
from app.library.uploads import MAX_FILES, MAX_MODEL_BYTES, Plan, UploadedFile
from app.services.assets import AssetListing, AssetRow
from app.services.changes import MAX_NOTE

# A file's contents travel as base64, which is a third longer than they are.
_MAX_ENCODED = (MAX_MODEL_BYTES * 4) // 3 + 8


class FileBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # The file's bytes, as base64: a STEP model need not be text.
    content: str = Field(max_length=_MAX_ENCODED)


class UploadBody(BaseModel):
    """Files to add to one library."""

    library: str = Field(max_length=64)
    files: list[FileBody] = Field(default_factory=list, max_length=MAX_FILES)
    # Said once the page has shown what would be replaced.
    replace: bool = False
    note: str = Field("", max_length=MAX_NOTE)

    def uploaded(self) -> list[UploadedFile]:
        files: list[UploadedFile] = []
        problems: list[str] = []
        for file in self.files:
            try:
                data = base64.b64decode(file.content, validate=True)
            except (binascii.Error, ValueError):
                problems.append(f"{file.name} did not arrive whole; choose it again.")
                continue
            # Only the name: a browser may send where the file was on its disk.
            files.append(UploadedFile(file.name.replace("\\", "/").rsplit("/", 1)[-1], data))
        if problems:
            raise EditError(problems)
        return files


class UploadItemOut(BaseModel):
    kind: str  # symbol | footprint | model
    name: str
    ref: str  # how a part or a footprint names it
    source_file: str  # the uploaded file it came out of
    path: str  # where it goes, from the top of the repository
    replaces: bool
    used_by: list[str]  # what uses what it replaces
    drawing: str | None  # SVG, for a symbol or a footprint


class UploadOutcome(BaseModel):
    """What an upload does, or would do, to the library."""

    library: str
    items: list[UploadItemOut]
    # Reasons it cannot be added; empty when it can.
    problems: list[str]
    # What was changed in the files on the way, or is worth knowing.
    notes: list[str]
    replaces: bool

    @classmethod
    def of(cls, plan: Plan) -> UploadOutcome:
        return cls(
            library=plan.library,
            items=[
                UploadItemOut(
                    kind=item.kind.value,
                    name=item.name,
                    ref=item.ref(plan.library),
                    source_file=item.source_file,
                    path=item.path,
                    replaces=item.replaces,
                    used_by=list(item.used_by),
                    drawing=item.drawing,
                )
                for item in plan.items
            ],
            problems=plan.problems,
            notes=plan.notes,
            replaces=plan.replaces,
        )


class AssetOut(BaseModel):
    kind: str
    library: str
    name: str
    ref: str
    source: str
    path: str
    used_by: list[str]

    @classmethod
    def of(cls, row: AssetRow) -> AssetOut:
        return cls(
            kind=row.kind.value,
            library=row.library,
            name=row.name,
            ref=row.ref,
            source=row.source,
            path=row.path,
            used_by=list(row.used_by),
        )


class AssetList(BaseModel):
    """The library's symbols, footprints or 3D models."""

    total: int  # matching the search, before the limit
    returned: int
    assets: list[AssetOut]

    @classmethod
    def of(cls, listing: AssetListing) -> AssetList:
        return cls(
            total=listing.total,
            returned=len(listing.rows),
            assets=[AssetOut.of(row) for row in listing.rows],
        )


class AssetRemoved(BaseModel):
    kind: str
    ref: str
