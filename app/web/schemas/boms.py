"""BOMs over the API: uploading them, reading them, and the product tree."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.boms.report import BomReport
from app.boms.store import Bom, Usage
from app.boms.tree import ProductTree
from app.domain.parts import Part
from app.services.boms import BomUpload
from app.services.costing import Costing, LineCost

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


# -- what a BOM costs -------------------------------------------------------------


class PurchaseOut(BaseModel):
    """Where a line is bought, and what it comes to."""

    supplier: str  # its id: "digikey"
    name: str  # what it is called
    sku: str  # the supplier's own number for the part
    mpn: str  # the manufacturer part bought, as the library has it
    manufacturer: str  # as the supplier calls it
    preferred: bool  # whether it is the part's preferred source
    quantity: int  # how many are bought: what is needed, or the smallest order
    surplus: int  # how many of those are more than needed
    unit_price: float  # each, at that quantity
    cost: float  # for the line
    currency: str
    stock: int | None  # what the supplier has; None if it does not say
    # "enough", "short", or "unknown" where it does not say.
    in_stock: str
    lead_weeks: int | None
    status: str  # in the supplier's words
    url: str | None  # the part's page at the supplier
    asked_at: float | None  # when the supplier said so; epoch seconds
    old: bool  # whether that answer no longer holds, and is shown for want of one


class CostLine(BaseModel):
    item: int
    references: list[str]
    quantity: int  # on one board
    needed: int  # for all the boards; 0 where it is not fitted
    dnp: bool
    ipn: str | None
    description: str
    bought: PurchaseOut | None
    # Why it has no price: "not fitted", "not in the library", "not asked
    # yet", "no supplier lists it", and so on. Empty where it has one.
    note: str
    # Whether the supplier it is bought from has the part as worse than the
    # library does: obsolete, or not for new designs.
    warns: bool

    @classmethod
    def of(cls, row: LineCost, costing: Costing) -> CostLine:
        bought = row.bought
        return cls(
            item=row.item,
            references=list(row.line.references),
            quantity=row.line.quantity,
            needed=row.needed,
            dnp=row.line.dnp,
            ipn=row.line.ipn,
            description=row.part.description if row.part else "",
            bought=None
            if bought is None
            else PurchaseOut(
                supplier=bought.offer.supplier,
                name=costing.name_of(bought.offer.supplier),
                sku=bought.offer.sku,
                mpn=bought.mpn,
                manufacturer=bought.offer.manufacturer,
                preferred=bought.preferred,
                quantity=bought.quantity,
                surplus=bought.surplus,
                unit_price=bought.unit_price,
                cost=bought.cost,
                currency=bought.currency,
                stock=bought.offer.stock,
                in_stock=bought.stock,
                lead_weeks=bought.offer.lead_weeks,
                status=bought.offer.status,
                url=bought.offer.url,
                asked_at=bought.asked_at,
                old=bought.old,
            ),
            note=row.note,
            warns=row.warns,
        )


class CostTotal(BaseModel):
    currency: str
    total: float  # for all the boards
    per_board: float


class BomCost(BaseModel):
    """What building a number of boards from a BOM costs, from what the
    suppliers have said."""

    id: int
    boards: int
    # What the costing is totalled in: the currency most prices are in.
    currency: str
    # One for each currency there are prices in, that one first. Prices in
    # two currencies are two totals: no rate is guessed at.
    totals: list[CostTotal]
    fitted: int  # lines that are fitted
    priced: int  # of those, the ones with a price
    # How many answers the BOM's manufacturer parts need -- one from each
    # supplier for each -- and how many of those are not here yet, or old.
    questions: int
    unasked: int
    suppliers: bool  # whether there is any supplier to ask
    lines: list[CostLine]

    @classmethod
    def of(cls, costing: Costing) -> BomCost:
        return cls(
            id=costing.bom.id,
            boards=costing.boards,
            currency=costing.currency,
            totals=[
                CostTotal(currency=currency, total=total, per_board=total / costing.boards)
                for currency, total in costing.totals
            ],
            fitted=len(costing.fitted),
            priced=costing.priced,
            questions=costing.questions,
            unasked=costing.unasked,
            suppliers=costing.suppliers,
            lines=[CostLine.of(row, costing) for row in costing.lines],
        )
