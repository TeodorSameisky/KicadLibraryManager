"""What suppliers say about a part: over HTTP.

Asked for by the part's page once it is on screen, and not while the page is
being made: a supplier takes its time to answer, and the page should not wait
on somebody else's server.

A plain function rather than a coroutine. It calls out to suppliers and talks
to SQLite, both of which block, so it runs on the server's worker threads.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.domain.parts import Part
from app.library.snapshot import Snapshot
from app.services.supply import Supply
from app.web.deps import get_snapshot, get_supply, require_access, require_part
from app.web.schemas.supply import PartSupply

router = APIRouter(prefix="/api/v1", tags=["supply"], dependencies=[Depends(require_access)])


@router.get("/parts/{ipn}/supply")
def part_supply(
    part: Part = Depends(require_part),
    snapshot: Snapshot = Depends(get_snapshot),
    supply: Supply = Depends(get_supply),
) -> PartSupply:
    """Stock, status and prices for each manufacturer part the part lists,
    from every supplier this deployment can ask.

    An answer already here is not asked for again while it holds, so this
    costs a call to a supplier only the first time in a day a part is looked
    at. With no supplier set up, each manufacturer part has no answers.
    """
    return PartSupply.of(part, supply.for_part(part, snapshot))
