"""A part's history: what was changed in it, by whom, and when."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.config import Settings
from app.domain.parts import Part
from app.library.history import DEFAULT_LIMIT, History, HistoryUnavailable, commit_url
from app.library.service import LibraryService
from app.web.deps import get_library, get_settings, require_access, require_part
from app.web.schemas.library import PartHistory
from app.web.templating import render

api = APIRouter(prefix="/api/v1", tags=["history"])
pages = APIRouter(include_in_schema=False)


def _commit_link(library: LibraryService, settings: Settings, commit: str) -> str | None:
    """The git host's page for a commit, if it has one we can name."""
    wanted = library.snapshot.parts_source
    source = next((s for s in settings.library_sources if s.id == wanted), None)
    return commit_url(source.url, commit) if source else None


def _links(library: LibraryService, settings: Settings, history: History) -> dict[str, str | None]:
    """Each change's commit on the git host, by commit."""
    return {c.commit: _commit_link(library, settings, c.commit) for c in history.changes}


def _history(library: LibraryService, part: Part) -> History:
    return library.history(part.path, DEFAULT_LIMIT)


# Plain functions: reading a repository runs git, which blocks, so these run
# on the server's worker threads instead of on the event loop.


@api.get("/parts/{ipn}/history")
def part_history(
    part: Part = Depends(require_part),
    library: LibraryService = Depends(get_library),
    settings: Settings = Depends(get_settings),
) -> PartHistory:
    history = _history(library, part)
    return PartHistory.of(part, history, _links(library, settings, history))


@pages.get("/ipn/{ipn}/history")
def history_page(
    request: Request,
    part: Part = Depends(require_part),
    library: LibraryService = Depends(get_library),
    settings: Settings = Depends(get_settings),
) -> Response:
    unavailable = None
    history = History(changes=())
    try:
        history = _history(library, part)
    except HistoryUnavailable as exc:
        unavailable = str(exc)

    return render(
        request,
        "history.html",
        part=part,
        history=history,
        unavailable=unavailable,
        links=_links(library, settings, history),
        limit=DEFAULT_LIMIT,
    )


router = APIRouter(dependencies=[Depends(require_access)])
router.include_router(api)
router.include_router(pages)
