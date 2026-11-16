"""The product tree: families, the devices in each, and each device's BOMs.

What is a product is written in the library's repository, in `products/`. This
only shows it, joined with the BOMs that have been uploaded for each device.
Like the other BOM routes these are plain functions, run on worker threads,
because listing the BOMs talks to SQLite.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.boms.store import BomStore, StoreUnavailable
from app.boms.tree import ProductTree, build_tree
from app.library.snapshot import Snapshot
from app.web.deps import get_boms, get_snapshot, require_access
from app.web.schemas.boms import ProductTreeOut
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
) -> ProductTreeOut:
    tree, problem = _tree(boms, snapshot)
    return ProductTreeOut.of(tree, bom_problem=problem is not None)


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
