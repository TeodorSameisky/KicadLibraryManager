"""Editing the templates that say what each category's parts carry.

A template decides what every part in a category must say about itself, so
all of this is a librarian's to do. Each change is one commit to the library's
repository, in the name of whoever made it, like any other change to it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth.sessions import Session
from app.config import Settings
from app.library import template_editing as editing
from app.library.editing import EditError
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.library.template_editing import FieldSpec, TemplateForm
from app.web.deps import get_library, get_settings, get_snapshot, require_access, require_librarian
from app.web.routes.editing import _author, _publish_snapshot, _refused, _require_writable
from app.web.templating import render

api = APIRouter(prefix="/api/v1/templates", tags=["templates"])
pages = APIRouter(include_in_schema=False)


def _require_category(category: str, snapshot: Snapshot = Depends(get_snapshot)) -> str:
    if category not in snapshot.parts.categories:
        raise HTTPException(status_code=404, detail=f"There is no category {category}")
    return category


# -- what is asked for ------------------------------------------------------


class FieldBody(BaseModel):
    name: str = Field(max_length=60)
    required: bool = False
    options: list[str] = Field(default_factory=list, max_length=editing.MAX_OPTIONS + 1)
    example: str = Field("", max_length=100)
    default: str = Field("", max_length=200)


class TemplateBody(BaseModel):
    value: str = Field("", max_length=200)
    fields: list[FieldBody] = Field(default_factory=list, max_length=editing.MAX_FIELDS + 1)

    def form(self) -> TemplateForm:
        return TemplateForm(
            value=self.value,
            fields=tuple(
                FieldSpec(
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


def _outcome(snapshot: Snapshot, category: str) -> dict[str, Any]:
    """What a saved template means for the parts that already exist."""
    short = editing.shortfall(snapshot, category)
    return {"category": category, "parts": short.of, "falling_short": short.parts}


# -- as JSON ----------------------------------------------------------------


@api.put("/{category}", dependencies=[Depends(require_librarian)])
async def save_template(
    body: TemplateBody,
    category: str = Depends(_require_category),
    settings: Settings = Depends(get_settings),
    snapshot: Snapshot = Depends(get_snapshot),
    library: LibraryService = Depends(_require_writable),
    session: Session | None = Depends(require_access),
) -> Any:
    """Create a category's template, or replace it."""
    form = body.form()
    try:
        editing.check_template(form, snapshot, category)
    except EditError as exc:
        return _refused(exc)

    name = snapshot.category_name(category)
    after, failed = await _publish_snapshot(
        library,
        lambda root: editing.write_template(root, category, name, form),
        _author(session, settings),
    )
    return failed or _outcome(after, category)


@api.delete("/{category}", dependencies=[Depends(require_librarian)])
async def remove_template(
    category: str = Depends(_require_category),
    settings: Settings = Depends(get_settings),
    snapshot: Snapshot = Depends(get_snapshot),
    library: LibraryService = Depends(_require_writable),
    session: Session | None = Depends(require_access),
) -> Any:
    """Take a template away: the category's parts are then asked for nothing."""
    name = snapshot.category_name(category)
    after, failed = await _publish_snapshot(
        library,
        lambda root: editing.delete_template(root, category, name),
        _author(session, settings),
    )
    return failed or _outcome(after, category)


# -- as pages ---------------------------------------------------------------


@pages.get("/templates", dependencies=[Depends(require_librarian)])
async def templates_page(
    request: Request,
    snapshot: Snapshot = Depends(get_snapshot),
    library: LibraryService = Depends(get_library),
) -> Response:
    counts: dict[str, int] = {}
    for part in snapshot.parts.parts.values():
        counts[part.category] = counts.get(part.category, 0) + 1

    rows = []
    for category in sorted(snapshot.parts.categories.values(), key=lambda c: c.code):
        template = snapshot.parts.templates.get(category.code)
        rows.append(
            {
                "code": category.code,
                "name": category.name,
                "parts": counts.get(category.code, 0),
                "template": template,
                "required": sum(spec.required for spec in template.fields) if template else 0,
                "falling_short": editing.shortfall(snapshot, category.code).parts
                if template
                else 0,
            }
        )
    return render(request, "templates.html", rows=rows, writable=library.writable)


@pages.get("/templates/{category}/edit", dependencies=[Depends(require_librarian)])
async def edit_template_page(
    request: Request,
    category: str = Depends(_require_category),
    snapshot: Snapshot = Depends(get_snapshot),
    library: LibraryService = Depends(get_library),
) -> Response:
    template = snapshot.parts.templates.get(category)
    return render(
        request,
        "template_form.html",
        category=category,
        category_name=snapshot.category_name(category),
        has_template=template is not None,
        data=editing.form_from(template),
        reserved=list(editing.RESERVED_FIELDS),
        writable=library.writable,
    )


router = APIRouter(dependencies=[Depends(require_access)])
router.include_router(api)
router.include_router(pages)
