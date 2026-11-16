"""Editing the templates that say what each category's parts carry: over HTTP.

What may be done, by whom, and what it takes is `app.services.templates`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.domain.policy import Actor
from app.domain.templates import RESERVED_FIELDS
from app.library import template_editing as editing
from app.library.service import LibraryService
from app.library.snapshot import Snapshot
from app.services import templates
from app.web.deps import get_actor, get_library, get_snapshot, require_access, require_librarian
from app.web.schemas.library import NoteBody, TemplateBody, TemplateOutcome
from app.web.templating import render

api = APIRouter(prefix="/api/v1/templates", tags=["templates"])
pages = APIRouter(include_in_schema=False)


def _require_category(category: str, snapshot: Snapshot = Depends(get_snapshot)) -> str:
    return templates.require_category(snapshot, category)


# -- as JSON ----------------------------------------------------------------


@api.put("/{category}")
async def save_template(
    category: str,
    body: TemplateBody,
    library: LibraryService = Depends(get_library),
    actor: Actor = Depends(get_actor),
) -> TemplateOutcome:
    """Create a category's template, or replace it."""
    short = await templates.save_template(
        library, category, body.form(), note=body.note, actor=actor
    )
    return TemplateOutcome.of(category, short)


@api.delete("/{category}")
async def remove_template(
    category: str,
    body: NoteBody | None = None,
    library: LibraryService = Depends(get_library),
    actor: Actor = Depends(get_actor),
) -> TemplateOutcome:
    """Take a template away: the category's parts are then asked for nothing."""
    short = await templates.remove_template(
        library, category, note=body.note if body else "", actor=actor
    )
    return TemplateOutcome.of(category, short)


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
        reserved=list(RESERVED_FIELDS),
        writable=library.writable,
    )


router = APIRouter(dependencies=[Depends(require_access)])
router.include_router(api)
router.include_router(pages)
