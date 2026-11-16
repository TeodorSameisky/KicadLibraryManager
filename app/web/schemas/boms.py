"""BOMs over the API: uploading them, reading them, and the product tree."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.boms.report import BomReport
from app.boms.store import Bom, Usage
from app.boms.tree import ProductTree
from app.domain.parts import Part
from app.services.boms import BomUpload

# A BOM of ten thousand lines is under a megabyte.
MAX_BOM_CHARACTERS = 4 * 1024 * 1024


class BomSummary(BaseModel):
    id: int
    project: str
    revision: str
    filename: str
    note: str
    uploaded_at: float  # epoch seconds
    uploaded_by: str
    # The device this is the BOM of, as "family/device"; empty for none.
    device: str
    lines: int
    components: int  # fitted; DNP lines are not counted

    @classmethod
    def of(cls, bom: Bom) -> BomSummary:
        return BomSummary(
            id=bom.id,
            project=bom.project,
            revision=bom.revision,
            filename=bom.filename,
            note=bom.note,
            uploaded_at=bom.uploaded_at,
            uploaded_by=bom.uploaded_by,
            device=bom.device,
            lines=bom.line_count,
            components=bom.component_count,
        )


def _summary(bom: Bom) -> dict[str, object]:
    """A BOM's own fields, for the models that begin with them."""
    return BomSummary.of(bom).model_dump()


class BomList(BaseModel):
    boms: list[BomSummary]


class BomUploaded(BomSummary):
    """A BOM as stored, and what the reader had to say about the file."""

    warnings: list[str]

    @classmethod
    def of_upload(cls, bom: Bom, warnings: tuple[str, ...]) -> BomUploaded:
        return cls.model_validate({**_summary(bom), "warnings": list(warnings)})


class BomConflict(BaseModel):
    """A BOM is already stored for this project and revision: which one."""

    detail: str
    existing: BomSummary


class BomAttached(BaseModel):
    id: int
    device: str


class RemarkOut(BaseModel):
    """Something about a line that someone should look at."""

    model_config = ConfigDict(from_attributes=True)

    level: str  # "bad": needs fixing. "warn": worth knowing.
    text: str


class BomItem(BaseModel):
    """One line of a BOM, looked up in the library as it is now."""

    references: list[str]
    quantity: int
    dnp: bool
    ipn: str | None  # None for what is not a library part: a mounting hole
    value: str
    footprint: str
    known: bool  # whether the library has this part
    description: str
    status: str | None
    mpn: str | None  # the preferred manufacturer part
    manufacturer: str | None
    lifecycle: str | None
    alternates: list[str]
    remarks: list[RemarkOut]


class BomDetail(BomSummary):
    """A BOM with every line looked up in the library as it is now."""

    parts: int  # how many different library parts it calls for
    problems: int
    cautions: int
    items: list[BomItem]

    @classmethod
    def of_report(cls, report: BomReport) -> BomDetail:
        items = [
            BomItem(
                references=list(row.line.references),
                quantity=row.line.quantity,
                dnp=row.line.dnp,
                ipn=row.line.ipn,
                value=row.line.value,
                footprint=row.line.footprint,
                known=row.part is not None,
                description=row.description,
                status=row.part.status if row.part else None,
                mpn=row.preferred_mpn or None,
                manufacturer=row.source.manufacturer if row.source else None,
                lifecycle=row.source.lifecycle if row.source else None,
                alternates=list(row.alternates),
                remarks=[RemarkOut.model_validate(remark) for remark in row.remarks],
            )
            for row in report.lines
        ]
        return cls.model_validate(
            {
                **_summary(report.bom),
                "parts": report.part_count,
                "problems": report.problems,
                "cautions": report.cautions,
                "items": items,
            }
        )


class UsageOut(BaseModel):
    """One part's place on one BOM."""

    bom: BomSummary
    quantity: int
    references: list[str]
    dnp: bool


class WhereUsed(BaseModel):
    """Every stored BOM a part is on."""

    ipn: str
    used_in: list[UsageOut]

    @classmethod
    def of(cls, part: Part, usages: list[Usage]) -> WhereUsed:
        return cls(
            ipn=part.ipn,
            used_in=[
                UsageOut(
                    bom=BomSummary.of(usage.bom),
                    quantity=usage.quantity,
                    references=list(usage.references),
                    dnp=usage.dnp,
                )
                for usage in usages
            ],
        )


# -- what is sent ---------------------------------------------------------------


class Upload(BaseModel):
    """A BOM exported from KiCad, as text.

    Sent as JSON rather than as a form upload, so that a script -- or a KiCad
    plugin, one day -- can post one as easily as the page does.
    """

    project: str = Field(min_length=1, max_length=200)
    revision: str = Field("", max_length=50)
    note: str = Field("", max_length=2000)
    filename: str = Field("", max_length=255)
    content: str = Field(min_length=1, max_length=MAX_BOM_CHARACTERS)
    # The device this is the BOM of, as "family/device" in the product tree;
    # empty when it is not for one of them.
    device: str = Field("", max_length=200)
    # Uploading a project's revision a second time replaces the first only
    # when this is set: overwriting a record should be chosen, not stumbled into.
    replace: bool = False

    def asked(self) -> BomUpload:
        return BomUpload(
            project=self.project,
            content=self.content,
            revision=self.revision,
            note=self.note,
            filename=self.filename,
            device=self.device,
            replace=self.replace,
        )


class DeviceChoice(BaseModel):
    device: str = Field("", max_length=200)


# -- products -------------------------------------------------------------------


class DeviceOut(BaseModel):
    id: str  # unique within the family
    key: str  # "family/device": what a BOM is attached by
    name: str
    description: str
    repository: str
    tags: list[str]
    boms: list[BomSummary]  # newest first


class FamilyOut(BaseModel):
    id: str
    name: str
    description: str
    repository: str
    tags: list[str]
    devices: list[DeviceOut]


class ProductTreeOut(BaseModel):
    """Families, the devices in each, and each device's BOMs."""

    tags: list[str]
    # Whether the BOMs could not be read: the tree is then shown without them.
    bom_problem: bool
    families: list[FamilyOut]
    # BOMs that are on no device in the tree.
    unassigned: list[BomSummary]

    @classmethod
    def of(cls, tree: ProductTree, *, bom_problem: bool) -> ProductTreeOut:
        return cls(
            tags=list(tree.tags),
            bom_problem=bom_problem,
            families=[
                FamilyOut(
                    id=node.family.id,
                    name=node.family.name,
                    description=node.family.description,
                    repository=node.family.repository,
                    tags=list(node.family.tags),
                    devices=[
                        DeviceOut(
                            id=entry.device.id,
                            key=entry.device.key,
                            name=entry.device.name,
                            description=entry.device.description,
                            repository=entry.device.repository,
                            tags=list(entry.device.tags),
                            boms=[BomSummary.of(bom) for bom in entry.boms],
                        )
                        for entry in node.devices
                    ],
                )
                for node in tree.families
            ],
            unassigned=[BomSummary.of(bom) for bom in tree.unassigned],
        )
