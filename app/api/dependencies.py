"""Shared dependencies for the catalog routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request

from app.kicad.library import normalise_model_ref
from app.kicad.parts import Part, PartsIndex
from app.kicad.ref import LibraryRef
from app.library_service import LibraryService


def get_service(request: Request) -> LibraryService:
    return request.app.state.library


def require_part(ipn: str, service: LibraryService) -> Part:
    part = service.get_part(ipn)
    if part is None:
        raise HTTPException(status_code=404, detail=f"No part {ipn}")
    return part


def part_summary(part: Part, parts_index: PartsIndex) -> dict:
    preferred = part.preferred_mpn
    manufacturer = ""
    if preferred and preferred in parts_index.mpns:
        manufacturer = parts_index.mpns[preferred].manufacturer
    return {
        "ipn": part.ipn,
        "description": part.description,
        "status": part.status,
        "preferred_mpn": preferred,
        "manufacturer": manufacturer,
        "mpn_count": len(part.mpns),
    }


def part_sources(part: Part, parts_index: PartsIndex) -> list[dict]:
    out = []
    for ref in part.mpns:
        mpn = parts_index.mpns.get(ref.mpn)
        out.append(
            {
                "mpn": ref.mpn,
                "preferred": ref.preferred,
                "manufacturer": mpn.manufacturer if mpn else None,
                "datasheet": mpn.datasheet if mpn else None,
                "lifecycle": mpn.lifecycle if mpn else "unknown",
                "known": mpn is not None,
            }
        )
    return out


def find_model(part: Part, snapshot) -> Path | None:
    """The 3D model a part resolves to, or None.

    Reached through the footprint: a symbol never names a model, and the
    reference lives in the .kicad_mod alongside its placement offsets.
    """
    ref = LibraryRef.parse(part.footprint)
    if ref is None:
        return None

    located = snapshot.catalog.find_footprint(ref.library, ref.name)
    if not located:
        return None

    for model_ref in located[0].asset.model_refs:
        found = snapshot.catalog.find_model(normalise_model_ref(model_ref))
        if found and not found[0].asset.is_lfs_pointer:
            return Path(found[0].asset.path)
    return None
