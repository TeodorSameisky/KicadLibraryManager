"""Telling the app that the library may have changed.

Two callers. A librarian, who presses a button and wants to see what came of
it. And the git host, which calls a webhook the moment something is pushed and
wants nothing back but a prompt answer.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import Settings
from app.library.service import LibraryService
from app.web.deps import get_library, get_settings, require_librarian

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/library", tags=["library"])


@router.post("/refresh", dependencies=[Depends(require_librarian)])
async def refresh(library: LibraryService = Depends(get_library)) -> dict[str, Any]:
    """Fetch every source now, and index whatever changed.

    Answers when it is done, so that the button that asked can say what
    happened: nothing new, a new index, or a source that could not be reached.
    """
    before = library.snapshot
    after = await library.refresh()
    return {
        "state": library.state.value,
        "error": library.error,
        "changed": after is not before,
        "parts": after.part_count,
        "errors": len(after.errors),
        "warnings": len(after.warnings),
        "checked_at": library.checked_at,
        "sources": [
            {"id": source.id, "commit": source.commit, "error": source.error}
            for source in after.sources
        ],
    }


def _signatures(request: Request) -> list[str]:
    """The signatures a git host sent, as bare hex.

    GitHub sends `X-Hub-Signature-256: sha256=<hex>`. Gitea sends the same and
    also `X-Gitea-Signature: <hex>`. Both are an HMAC-SHA256 of the body.
    """
    found = []
    for header in ("x-hub-signature-256", "x-gitea-signature"):
        value = request.headers.get(header, "").strip()
        if value:
            found.append(value.removeprefix("sha256="))
    return found


@router.post("/webhook", status_code=202)
async def webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
    library: LibraryService = Depends(get_library),
) -> dict[str, str]:
    """Called by the git host when something is pushed.

    It has no session, so what lets it in is a secret it shares with this app:
    the request carries a signature of its own body made with that secret.
    Nothing in the body is used -- being called at all is the message -- so a
    replayed request can do no more than cause one more look at the library.
    """
    if not settings.webhook_secret:
        raise HTTPException(status_code=404, detail="No webhook is configured")

    expected = hmac.new(
        settings.webhook_secret.encode(), await request.body(), hashlib.sha256
    ).hexdigest()
    # Compared in constant time: how long a mismatch takes must not say how
    # much of a guess was right.
    if not any(hmac.compare_digest(expected, given) for given in _signatures(request)):
        raise HTTPException(status_code=401, detail="The signature does not match")

    # Not waited for: the git host gives a webhook ten seconds, and a library
    # with 3D models can take longer than that to fetch.
    library.request_refresh()
    log.info("the git host reported a push; refreshing the library")
    return {"status": "refresh requested"}
