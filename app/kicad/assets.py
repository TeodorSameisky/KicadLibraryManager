"""Assemble the assets KiCad is sent when a part is placed.

Each asset travels inline in the RPC envelope: zstd-compressed, base64-encoded.
Order matters. Footprints and 3D models are saved first, and the symbol goes
last with mode PLACE, so that by the time the symbol lands on the canvas the
things it references already exist locally.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

import zstandard

from app.kicad.build import DEFAULT_REMOTE_PREFIX, BuildError, build_symbol
from app.kicad.catalog import Catalog
from app.kicad.library import normalise_model_ref
from app.kicad.parts import Part, PartsIndex

CONTENT_TYPES = {
    "DL_SYMBOL": "KICAD_SYMBOL_V1",
    "DL_FOOTPRINT": "KICAD_FOOTPRINT_V1",
    "DL_3DMODEL": "KICAD_3D_MODEL_STEP",
    "DL_SPICE": "KICAD_SPICE_MODEL_V1",
}

# An inline payload crosses a WebView string bridge and must survive KiCad's
# response timeout. Large 3D models are the realistic offender.
INLINE_WARN_BYTES = 2 * 1024 * 1024


@dataclass
class Asset:
    command: str
    label: str
    filename: str
    data: str
    parameters: dict
    size_bytes: int


@dataclass
class AssetBundle:
    ipn: str
    assets: list[Asset] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_encoded(self) -> int:
        return sum(len(a.data) for a in self.assets)


def encode(raw: bytes) -> str:
    """zstd then base64, as KiCad's inline payload path expects."""
    return base64.b64encode(zstandard.ZstdCompressor().compress(raw)).decode("ascii")


def _asset(
    command: str, label: str, filename: str, raw: bytes, mode: str, library: str, name: str
) -> Asset:
    return Asset(
        command=command,
        label=label,
        filename=filename,
        data=encode(raw),
        parameters={
            "mode": mode,
            "compression": "ZSTD",
            "content_type": CONTENT_TYPES[command],
            "library": library,
            "name": name,
        },
        size_bytes=len(raw),
    )


def build_assets(
    part: Part,
    catalog: Catalog,
    parts_index: PartsIndex,
    public_url: str,
    remote_prefix: str = DEFAULT_REMOTE_PREFIX,
) -> AssetBundle:
    """Everything KiCad needs to place `part`, in the order it should be sent."""
    bundle = AssetBundle(ipn=part.ipn)

    symbol = build_symbol(part, catalog, parts_index, public_url, remote_prefix)

    footprint = None
    if part.footprint and ":" in part.footprint:
        fp_lib, fp_name = part.footprint.split(":", 1)
        located = catalog.find_footprint(fp_lib, fp_name)
        if located:
            footprint = located[0].asset
        else:
            bundle.warnings.append(
                f"footprint {part.footprint!r} is not available; the placed part "
                "will have no land pattern"
            )

    if footprint is not None:
        try:
            raw = Path(footprint.path).read_bytes()
        except OSError as exc:
            raise BuildError(f"cannot read {footprint.path}: {exc}") from exc

        bundle.assets.append(
            _asset(
                "DL_FOOTPRINT",
                "Footprint",
                Path(footprint.path).name,
                raw,
                mode="SAVE",
                library=footprint.library,
                name=footprint.name,
            )
        )

        for ref in footprint.model_refs:
            key = normalise_model_ref(ref)
            found = catalog.find_model(key)
            if not found:
                bundle.warnings.append(f"3D model {key!r} is not available")
                continue

            model = found[0].asset
            if model.is_lfs_pointer:
                bundle.warnings.append(
                    f"3D model {key!r} is a Git LFS pointer, not the model itself"
                )
                continue

            raw = Path(model.path).read_bytes()
            if len(raw) > INLINE_WARN_BYTES:
                bundle.warnings.append(
                    f"3D model {model.name} is {len(raw) // 1024} KB; large inline "
                    "payloads can exceed KiCad's response timeout"
                )
            bundle.assets.append(
                _asset(
                    "DL_3DMODEL",
                    "3D Model",
                    model.name,
                    raw,
                    mode="SAVE",
                    library=model.library,
                    name=model.name,
                )
            )

    # Last, and the only one that places anything on the canvas.
    bundle.assets.append(
        _asset(
            "DL_SYMBOL",
            "Symbol",
            symbol.filename,
            symbol.text.encode("utf-8"),
            mode="PLACE",
            library=symbol.library,
            name=symbol.name,
        )
    )
    return bundle
