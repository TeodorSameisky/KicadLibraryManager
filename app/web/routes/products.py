"""The product tree: families, the devices in each, and each device's BOMs.

What is a product is written in the library's repository, in `products/`. This
only shows it, joined with the BOMs that have been uploaded for each device.
Like the other BOM routes these are plain functions, run on worker threads,
because listing the BOMs talks to SQLite.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.boms.store import BomStore, StoreUnavailable
from app.boms.tree import ProductTree, build_tree
from app.library.snapshot import Snapshot
from app.web.deps import get_boms, get_snapshot, require_access
from app.web.routes.boms import _summary
from app.web.templating import render

api = APIRouter(prefix="/api/v1", tags=["products"])
pages = APIRouter(include_in_schema=False)


def _tree(boms: BomStore, snapshot: Snapshot) -> tuple[ProductTree, str | None]:
    """The tree, and why it has no BOMs if the database could not be read."""
    try:
        return build_tree(snapshot.products, boms.list()), None
    except StoreUnavailable as exc:
        return build_tree(snapshot.products, ()), str(exc)


@api.get("/products")
def list_products(
    boms: BomStore = Depends(get_boms), snapshot: Snapshot = Depends(get_snapshot)
) -> dict[str, Any]:
    tree, problem = _tree(boms, snapshot)
    return {
        "tags": list(tree.tags),
        "bom_problem": problem is not None,
        "families": [
            {
                "id": node.family.id,
                "name": node.family.name,
                "description": node.family.description,
                "repository": node.family.repository,
                "tags": list(node.family.tags),
                "devices": [
                    {
                        "id": entry.device.id,
                        "key": entry.device.key,
                        "name": entry.device.name,
                        "description": entry.device.description,
                        "repository": entry.device.repository,
                        "tags": list(entry.device.tags),
                        "boms": [_summary(bom) for bom in entry.boms],
                    }
                    for entry in node.devices
                ],
            }
            for node in tree.families
        ],
        "unassigned": [_summary(bom) for bom in tree.unassigned],
    }


@pages.get("/products")
def products_page(
    request: Request,
    boms: BomStore = Depends(get_boms),
    snapshot: Snapshot = Depends(get_snapshot),
) -> Response:
    tree, problem = _tree(boms, snapshot)
    return render(request, "products.html", tree=tree, problem=problem)


router = APIRouter(dependencies=[Depends(require_access)])
router.include_router(api)
router.include_router(pages)
