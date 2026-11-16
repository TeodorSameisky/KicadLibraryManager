"""The library over the API: its index, its parts, and changing them."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.domain.forms import ManufacturerForm, PartForm, SourceEntry, TemplateForm
from app.domain.parameters import Parameter
from app.domain.parts import Lifecycle, Part, PartStatus
from app.domain.templates import Template, TemplateField
from app.library.history import History
from app.library.placement import AssetBundle
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.services import manufacturers as manufacturer_service
from app.services import templates
from app.services.changes import MAX_NOTE
from app.services.templates import Shortfall

# -- the index ----------------------------------------------------------------


class Facet(BaseModel):
    """One value a list can be narrowed to, and how many parts it would leave."""

    model_config = ConfigDict(from_attributes=True)

    value: str
    label: str
    count: int


class Facets(BaseModel):
    statuses: list[Facet]
    categories: list[Facet]


class SourceState(BaseModel):
    """One repository the library is read from, as last indexed."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    commit: str | None
    symbols: int
    footprints: int
    models: int
    parts: int
    error: str | None


class IndexStatus(BaseModel):
    """Where the index stands, source by source: where to look when
    something seems to be missing."""

    state: str
    error: str | None
    parts: int
    symbols: int
    footprints: int
    models: int
    errors: int
    warnings: int
    indexed_at: float | None
    duration_seconds: float
    # What the catalog can be narrowed by, so the panel offers the filters
    # this library supports rather than a fixed list.
    facets: Facets
    sources: list[SourceState]

    @classmethod
    def of(cls, library: LibraryService) -> IndexStatus:
        snapshot = library.snapshot
        return cls(
            state=library.state.value,
            error=library.error,
            parts=snapshot.part_count,
            symbols=snapshot.catalog.symbol_count,
            footprints=snapshot.catalog.footprint_count,
            models=snapshot.catalog.model_count,
            errors=len(snapshot.errors),
            warnings=len(snapshot.warnings),
            indexed_at=snapshot.indexed_at,
            duration_seconds=round(snapshot.duration, 1),
            facets=Facets(
                statuses=[Facet.model_validate(facet) for facet in snapshot.status_facets],
                categories=[Facet.model_validate(facet) for facet in snapshot.category_facets],
            ),
            sources=[SourceState.model_validate(source) for source in snapshot.sources],
        )


class RefreshedSource(BaseModel):
    id: str
    commit: str | None
    error: str | None


class RefreshResult(BaseModel):
    """What came of looking at the sources again."""

    state: str
    error: str | None
    changed: bool
    parts: int
    errors: int
    warnings: int
    checked_at: float | None
    sources: list[RefreshedSource]

    @classmethod
    def of(cls, library: LibraryService, after: Snapshot, *, changed: bool) -> RefreshResult:
        return cls(
            state=library.state.value,
            error=library.error,
            changed=changed,
            parts=after.part_count,
            errors=len(after.errors),
            warnings=len(after.warnings),
            checked_at=library.checked_at,
            sources=[
                RefreshedSource(id=source.id, commit=source.commit, error=source.error)
                for source in after.sources
            ],
        )


class IssueOut(BaseModel):
    severity: str
    kind: str
    message: str
    path: str | None
    source: str | None


class IssueList(BaseModel):
    """Everything in the library that does not line up."""

    total: int
    returned: int
    # The library's totals, not the page's: someone reading the errors can
    # still see that there are warnings to look at.
    errors: int
    warnings: int
    issues: list[IssueOut]

    @classmethod
    def of(cls, snapshot: Snapshot, severity: str | None, limit: int) -> IssueList:
        found = {"error": snapshot.errors, "warning": snapshot.warnings}.get(
            severity or "", list(snapshot.issues)
        )
        page = found[:limit]
        return cls(
            total=len(found),
            returned=len(page),
            errors=len(snapshot.errors),
            warnings=len(snapshot.warnings),
            issues=[
                IssueOut(
                    severity=issue.severity.value,
                    kind=issue.kind,
                    message=issue.message,
                    path=issue.path,
                    source=issue.source,
                )
                for issue in page
            ],
        )


# -- parts --------------------------------------------------------------------


class PartSummary(BaseModel):
    """A part as a row of the catalog."""

    ipn: str
    description: str
    status: str
    preferred_mpn: str | None
    manufacturer: str
    mpn_count: int

    @classmethod
    def of(cls, part: Part, snapshot: Snapshot) -> PartSummary:
        preferred = snapshot.mpn(part.preferred_mpn)
        return cls(
            ipn=part.ipn,
            description=part.description,
            status=part.status,
            preferred_mpn=part.preferred_mpn,
            manufacturer=preferred.manufacturer if preferred else "",
            mpn_count=len(part.mpns),
        )


class ParameterChoice(BaseModel):
    """One value a parameter can be narrowed to, and how many parts of
    the list it would leave."""

    model_config = ConfigDict(from_attributes=True)

    value: str
    count: int


class ParameterOut(BaseModel):
    """Something the parts of a category can be told apart by."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    # Whether its values are quantities, so that `>=` and `<=` can be asked.
    numeric: bool
    choices: list[ParameterChoice]


class PartList(BaseModel):
    state: str
    # `total` is the catalog and `matched` is what the filters selected.
    # "12 of 4000" says nothing about the search that produced the twelve.
    total: int
    matched: int
    returned: int
    parts: list[PartSummary]
    # What the list can be narrowed by next. Only within a category: what
    # a field means is the category's to say.
    parameters: list[ParameterOut]

    @classmethod
    def of(
        cls,
        library: LibraryService,
        found: list[Part],
        limit: int,
        parameters: Sequence[Parameter] = (),
    ) -> PartList:
        snapshot = library.snapshot
        page = found[:limit]
        return cls(
            state=library.state.value,
            total=snapshot.part_count,
            matched=len(found),
            returned=len(page),
            parts=[PartSummary.of(part, snapshot) for part in page],
            parameters=[ParameterOut.model_validate(parameter) for parameter in parameters],
        )


class PartSource(BaseModel):
    """A manufacturer part that may be bought for an internal part."""

    mpn: str
    preferred: bool
    # None, and `known` false, for one the part lists and the library does
    # not have a file for.
    manufacturer: str | None
    datasheet: str | None
    lifecycle: str
    known: bool


class PartDetail(BaseModel):
    ipn: str
    description: str
    status: str
    category: str
    symbol: str
    footprint: str
    fields: dict[str, str]
    mpns: list[PartSource]
    # Other parts that share a manufacturer part with this one.
    related: list[str]

    @classmethod
    def of(cls, part: Part, snapshot: Snapshot) -> PartDetail:
        sources = []
        for ref in part.mpns:
            mpn = snapshot.mpn(ref.mpn)
            sources.append(
                PartSource(
                    mpn=ref.mpn,
                    preferred=ref.preferred,
                    manufacturer=mpn.manufacturer if mpn else None,
                    datasheet=mpn.datasheet if mpn else None,
                    lifecycle=mpn.lifecycle if mpn else Lifecycle.UNKNOWN.value,
                    known=mpn is not None,
                )
            )
        return cls(
            ipn=part.ipn,
            description=part.description,
            status=part.status,
            category=part.category,
            symbol=part.symbol,
            footprint=part.footprint,
            fields=dict(part.fields),
            mpns=sources,
            related=[other.ipn for other in snapshot.related(part)],
        )


class PartRef(BaseModel):
    """A part that was just added or changed, and how it now stands."""

    ipn: str
    status: str


# -- placing --------------------------------------------------------------------


class AssetOut(BaseModel):
    """One thing KiCad is sent: a symbol, a footprint or a 3D model."""

    model_config = ConfigDict(from_attributes=True)

    command: str  # the RPC command that delivers it
    label: str
    filename: str
    parameters: dict[str, str]
    size_bytes: int  # before compression
    data: str  # zstd-compressed, base64-encoded


class AssetBundleOut(BaseModel):
    """Everything KiCad needs to place a part, in the order to send it."""

    ipn: str
    warnings: list[str]
    assets: list[AssetOut]

    @classmethod
    def of(cls, bundle: AssetBundle) -> AssetBundleOut:
        return cls(
            ipn=bundle.ipn,
            warnings=list(bundle.warnings),
            assets=[AssetOut.model_validate(asset) for asset in bundle.assets],
        )


# -- what a form is offered, and what it sends ----------------------------------


class CategoryChoice(BaseModel):
    code: str
    name: str


class TemplateFieldOut(BaseModel):
    name: str
    required: bool
    options: list[str]
    example: str
    default: str

    @classmethod
    def of(cls, expected: TemplateField) -> TemplateFieldOut:
        return cls(
            name=expected.name,
            required=expected.required,
            options=list(expected.options),
            example=expected.example,
            default=expected.default,
        )


class TemplateOut(BaseModel):
    """The fields a part in a category carries, for a form to lay out."""

    value: str
    fields: list[TemplateFieldOut]

    @classmethod
    def of(cls, template: Template) -> TemplateOut:
        return cls(
            value=template.value,
            fields=[TemplateFieldOut.of(expected) for expected in template.fields],
        )


class MpnChoice(BaseModel):
    mpn: str
    manufacturer: str
    lifecycle: str


class Choices(BaseModel):
    """What a part can be made from: for the form's lists."""

    writable: bool
    categories: list[CategoryChoice]
    symbols: list[str]
    footprints: list[str]
    templates: dict[str, TemplateOut]  # by category code
    mpns: list[MpnChoice]
    # Who may be named as making a new manufacturer part: the manufacturers
    # the library knows, and whoever its manufacturer parts already name.
    manufacturers: list[str]

    @classmethod
    def of(cls, snapshot: Snapshot, *, writable: bool) -> Choices:
        indexes = snapshot.catalog.indexes.values()
        return cls(
            writable=writable,
            categories=[
                CategoryChoice(code=category.code, name=category.name)
                for category in sorted(snapshot.parts.categories.values(), key=lambda c: c.code)
            ],
            symbols=sorted({f"{lib}:{name}" for index in indexes for lib, name in index.symbols}),
            footprints=sorted(
                {f"{lib}:{name}" for index in indexes for lib, name in index.footprints}
            ),
            templates={
                code: TemplateOut.of(template)
                for code, template in sorted(snapshot.parts.templates.items())
            },
            mpns=[
                MpnChoice(mpn=mpn.mpn, manufacturer=mpn.manufacturer, lifecycle=mpn.lifecycle)
                for mpn in sorted(snapshot.parts.mpns.values(), key=lambda m: m.mpn)
            ],
            manufacturers=sorted(
                {
                    *snapshot.parts.manufacturers.names,
                    *(mpn.manufacturer for mpn in snapshot.parts.mpns.values() if mpn.manufacturer),
                },
                key=str.casefold,
            ),
        )


class SourceBody(BaseModel):
    mpn: str = Field(min_length=1, max_length=120)
    preferred: bool = False
    # Only used when the library does not have this manufacturer part yet.
    manufacturer: str = Field("", max_length=120)
    datasheet: str = Field("", max_length=500)
    lifecycle: str = Field("active", max_length=20)


class PartBody(BaseModel):
    """A part as someone asks for it to be."""

    category: str = Field("", max_length=32)
    description: str = Field(max_length=300)
    symbol: str = Field(max_length=200)
    footprint: str = Field("", max_length=200)
    fields: dict[str, str] = Field(default_factory=dict)
    sources: list[SourceBody] = Field(default_factory=list)
    # Said by the person, for the library's history: what this is and why.
    note: str = Field("", max_length=MAX_NOTE)

    def form(self) -> PartForm:
        return PartForm(
            category=self.category,
            description=self.description,
            symbol=self.symbol.strip(),
            footprint=self.footprint.strip(),
            fields=dict(self.fields),
            sources=tuple(
                SourceEntry(
                    mpn=source.mpn.strip(),
                    preferred=source.preferred,
                    manufacturer=source.manufacturer,
                    datasheet=source.datasheet.strip(),
                    lifecycle=source.lifecycle,
                )
                for source in self.sources
            ),
        )


class StatusBody(BaseModel):
    status: str = Field(pattern=f"^({'|'.join(PartStatus)})$")
    note: str = Field("", max_length=MAX_NOTE)


# -- templates ------------------------------------------------------------------


class FieldBody(BaseModel):
    name: str = Field(max_length=60)
    required: bool = False
    options: list[str] = Field(default_factory=list, max_length=templates.MAX_OPTIONS + 1)
    example: str = Field("", max_length=100)
    default: str = Field("", max_length=200)


class TemplateBody(BaseModel):
    """A category's template as someone asks for it to be."""

    value: str = Field("", max_length=200)
    fields: list[FieldBody] = Field(default_factory=list, max_length=templates.MAX_FIELDS + 1)
    note: str = Field("", max_length=MAX_NOTE)

    def form(self) -> TemplateForm:
        return TemplateForm(
            value=self.value,
            fields=tuple(
                TemplateField(
                    name=item.name.strip(),
                    required=item.required,
                    # Blank options are the empty boxes of a form, not values.
                    options=tuple(option.strip() for option in item.options if option.strip()),
                    example=item.example,
                    default=item.default,
                )
                for item in self.fields
            ),
        )


class NoteBody(BaseModel):
    note: str = Field("", max_length=MAX_NOTE)


class TemplateOutcome(BaseModel):
    """What a saved template means for the parts that already exist."""

    category: str
    parts: int  # how many parts there are in the category
    falling_short: int  # and how many of them now fall short of it

    @classmethod
    def of(cls, category: str, short: Shortfall) -> TemplateOutcome:
        return cls(category=category, parts=short.of, falling_short=short.parts)


# -- manufacturers --------------------------------------------------------------


class ManufacturerOut(BaseModel):
    name: str  # what it is called, everywhere
    aliases: list[str]  # the other ways it gets written
    parts: int  # how many manufacturer parts it makes


class UnlistedOut(BaseModel):
    """A name some manufacturer part gives that the list does not recognise."""

    name: str  # as it is most often written
    spellings: list[str]  # every way it is written, that one first
    parts: int  # how many manufacturer parts give it


class ManufacturerList(BaseModel):
    """The manufacturers the library knows, and the names it does not."""

    writable: bool
    manufacturers: list[ManufacturerOut]  # by name
    unlisted: list[UnlistedOut]  # the most used first

    @classmethod
    def of(cls, overview: manufacturer_service.Overview, *, writable: bool) -> ManufacturerList:
        return cls(
            writable=writable,
            manufacturers=[
                ManufacturerOut(
                    name=listed.manufacturer.name,
                    aliases=list(listed.manufacturer.aliases),
                    parts=listed.parts,
                )
                for listed in overview.listed
            ],
            unlisted=[
                UnlistedOut(name=found.name, spellings=list(found.spellings), parts=found.parts)
                for found in overview.unlisted
            ],
        )


class ManufacturerBody(BaseModel):
    """A manufacturer as someone asks for it to be listed."""

    # The name it is listed under now, to change that one; left out to add one.
    was: str | None = Field(None, max_length=manufacturer_service.MAX_NAME)
    name: str = Field(max_length=200)
    aliases: list[str] = Field(
        default_factory=list, max_length=manufacturer_service.MAX_ALIASES + 1
    )
    note: str = Field("", max_length=MAX_NOTE)

    def form(self) -> ManufacturerForm:
        return ManufacturerForm(
            name=self.name,
            # Blank spellings are the empty boxes of a form, not spellings.
            aliases=tuple(alias.strip() for alias in self.aliases if alias.strip()),
        )


class ManufacturerRemoval(BaseModel):
    name: str = Field(max_length=manufacturer_service.MAX_NAME)
    note: str = Field("", max_length=MAX_NOTE)


# -- history --------------------------------------------------------------------


class ChangeOut(BaseModel):
    commit: str
    when: float  # epoch seconds
    author: str
    subject: str
    note: str  # what the person said about it, if they did
    what: list[str]  # what changed in the part, one sentence each
    url: str | None  # the commit on the git host, if it has a page for one


class PartHistory(BaseModel):
    ipn: str
    more: bool  # there are older changes than these
    changes: list[ChangeOut]  # newest first

    @classmethod
    def of(cls, part: Part, history: History, links: dict[str, str | None]) -> PartHistory:
        return cls(
            ipn=part.ipn,
            more=history.more,
            changes=[
                ChangeOut(
                    commit=change.commit,
                    when=change.when,
                    author=change.author,
                    subject=change.subject,
                    note=change.note,
                    what=list(change.what),
                    url=links.get(change.commit),
                )
                for change in history.changes
            ],
        )
