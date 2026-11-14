"""What KiCad is sent when a part is placed.

A library symbol is a template. What lands on the schematic is an IPN: the
same drawing, carrying the internal part number, the part's field values, its
preferred manufacturer part, and a link back to the part's page. So the symbol
is rewritten on its way out, and travels with the footprint and 3D model it
needs.

Each asset is an inline payload for KiCad's Remote Symbols RPC: zstd
compressed, base64 encoded, with parameters saying what it is and what to do
with it.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

import zstandard

from app.kicad import symbol as symbol_tree
from app.kicad.sexpr import ParseError, SExpr, dumps, loads
from app.library.assets import BrokenInheritance, Footprint, Symbol
from app.library.parts import Part
from app.library.refs import LibraryRef, model_key
from app.library.snapshot import Snapshot

# An inline payload crosses the WebView's string bridge and has to arrive
# inside KiCad's response timeout. A large 3D model is the realistic offender.
INLINE_WARN_BYTES = 2 * 1024 * 1024


class PlacementError(Exception):
    """A part cannot be turned into something worth placing.

    The message names library assets, never server paths, so it is safe to
    show and is what the person placing the part can act on.
    """


@dataclass(frozen=True)
class PlacementOptions:
    """The two facts about a deployment that end up inside a placed symbol."""

    public_url: str
    # KiCad files what it receives under "<prefix>_<library>". The prefix is
    # the user's to change in Remote Symbol Settings; "remote" is its default.
    remote_prefix: str = "remote"

    def part_url(self, ipn: str) -> str:
        return f"{self.public_url.rstrip('/')}/ipn/{ipn}"

    def remote_library(self, library: str) -> str:
        """The name KiCad gives a library it receives from a provider."""
        sanitised = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in library)
        return f"{self.remote_prefix}_{sanitised.lower()}"


@dataclass(frozen=True)
class PlacedSymbol:
    library: str
    name: str
    node: SExpr  # the finished (symbol ...), as it will be saved

    @property
    def text(self) -> str:
        return dumps(symbol_tree.library_document(self.node))


@dataclass(frozen=True)
class Asset:
    command: str  # the RPC command that delivers it
    label: str
    filename: str
    parameters: dict[str, str]
    size_bytes: int  # before compression
    data: str


@dataclass
class AssetBundle:
    ipn: str
    assets: list[Asset] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# -- the symbol -------------------------------------------------------------


def _read_symbol(symbol: Symbol) -> SExpr:
    try:
        node = loads(symbol.path.read_text(encoding="utf-8")).child("symbol")
    except (OSError, UnicodeDecodeError, ParseError) as exc:
        raise PlacementError(f"symbol {symbol.library}:{symbol.name} cannot be read") from exc
    if node is None:
        raise PlacementError(f"symbol {symbol.library}:{symbol.name} has no definition")
    return node


def placed_symbol(part: Part, snapshot: Snapshot, options: PlacementOptions) -> PlacedSymbol:
    """The symbol for `part`, self-contained and carrying the part's data."""
    ref = LibraryRef.parse(part.symbol)
    if ref is None:
        raise PlacementError(f"{part.ipn} names no usable symbol ({part.symbol!r})")

    try:
        ancestry = snapshot.catalog.symbol_ancestry(ref)
    except LookupError as exc:
        raise PlacementError(
            f"{part.ipn} names symbol {part.symbol!r}, which no source provides"
        ) from exc
    except BrokenInheritance as exc:
        raise PlacementError(
            f"{part.ipn} cannot be placed: {exc}, so the part would have no body"
        ) from exc

    # KiCad saves one symbol per payload, so the chain is collapsed into it.
    node = symbol_tree.flatten([_read_symbol(s) for s in reversed(ancestry)], ref.name)

    fields: dict[str, str] = {"IPN": part.ipn}
    if part.description:
        fields["Description"] = part.description

    # The part's page lists every approved source with its own datasheet,
    # which is more use than one manufacturer's PDF picked arbitrarily.
    fields["Datasheet"] = options.part_url(part.ipn)

    # The library's own field values go on top of the platform's.
    fields.update(part.fields)

    # Where KiCad will file the footprint, not where the library keeps it: a
    # symbol naming the source library points at one the user does not have.
    footprint = LibraryRef.parse(part.footprint)
    if footprint is not None:
        fields["Footprint"] = f"{options.remote_library(footprint.library)}:{footprint.name}"
    elif part.footprint:
        fields["Footprint"] = part.footprint

    preferred = snapshot.mpn(part.preferred_mpn)
    if part.preferred_mpn:
        fields["MPN"] = part.preferred_mpn
    if preferred is not None and preferred.manufacturer:
        fields["Manufacturer"] = preferred.manufacturer

    for name, value in fields.items():
        symbol_tree.set_property(node, name, value)

    return PlacedSymbol(library=ref.library, name=ref.name, node=node)


# -- the bundle -------------------------------------------------------------

_CONTENT_TYPES = {
    "DL_SYMBOL": "KICAD_SYMBOL_V1",
    "DL_FOOTPRINT": "KICAD_FOOTPRINT_V1",
    "DL_3DMODEL": "KICAD_3D_MODEL_STEP",
}


def encode(raw: bytes) -> str:
    """zstd, then base64: the encoding KiCad's inline payloads use."""
    return base64.b64encode(zstandard.ZstdCompressor().compress(raw)).decode("ascii")


def _asset(
    command: str, label: str, filename: str, raw: bytes, *, mode: str, library: str, name: str
) -> Asset:
    return Asset(
        command=command,
        label=label,
        filename=filename,
        parameters={
            "mode": mode,
            "compression": "ZSTD",
            "content_type": _CONTENT_TYPES[command],
            "library": library,
            "name": name,
        },
        size_bytes=len(raw),
        data=encode(raw),
    )


def _read_bytes(path: Path, what: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise PlacementError(f"{what} cannot be read") from exc


def _add_models(footprint: Footprint, snapshot: Snapshot, bundle: AssetBundle) -> None:
    for reference in footprint.model_refs:
        key = model_key(reference)
        model = snapshot.catalog.model(key)
        if model is None:
            bundle.warnings.append(f"3D model {key!r} is not available")
            continue
        if model.is_lfs_pointer:
            bundle.warnings.append(f"3D model {key!r} is a Git LFS pointer, not the model itself")
            continue

        raw = _read_bytes(model.path, f"3D model {key!r}")
        if len(raw) > INLINE_WARN_BYTES:
            bundle.warnings.append(
                f"3D model {model.name} is {len(raw) // 1024} KB; an inline payload "
                "that large can exceed KiCad's response timeout"
            )
        bundle.assets.append(
            _asset(
                "DL_3DMODEL",
                "3D model",
                model.name,
                raw,
                mode="SAVE",
                library=model.library,
                name=model.name,
            )
        )


def asset_bundle(part: Part, snapshot: Snapshot, options: PlacementOptions) -> AssetBundle:
    """Everything KiCad needs to place `part`, in the order to send it.

    The footprint and its 3D models go first and are only saved. The symbol
    goes last and is the one thing placed, so that by the time it lands on the
    sheet what it refers to is already there.

    A part whose symbol cannot be completed is refused outright. A missing
    footprint or model only warns: a symbol without a land pattern is still
    more use than nothing.
    """
    bundle = AssetBundle(ipn=part.ipn)
    symbol = placed_symbol(part, snapshot, options)

    ref = LibraryRef.parse(part.footprint)
    footprint = snapshot.catalog.footprint(ref) if ref else None

    if footprint is not None:
        bundle.assets.append(
            _asset(
                "DL_FOOTPRINT",
                "Footprint",
                footprint.path.name,
                _read_bytes(footprint.path, f"footprint {part.footprint!r}"),
                mode="SAVE",
                library=footprint.library,
                name=footprint.name,
            )
        )
        _add_models(footprint, snapshot, bundle)
    elif part.footprint:
        bundle.warnings.append(
            f"footprint {part.footprint!r} is not available; the placed part "
            "will have no land pattern"
        )

    bundle.assets.append(
        _asset(
            "DL_SYMBOL",
            "Symbol",
            f"{symbol.name}.kicad_sym",
            symbol.text.encode("utf-8"),
            mode="PLACE",
            library=symbol.library,
            name=symbol.name,
        )
    )
    return bundle
