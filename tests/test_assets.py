"""The asset bundle KiCad receives when a part is placed."""

import base64
import textwrap

import pytest
import zstandard

from app.kicad.assets import build_assets, encode
from app.kicad.build import BuildError
from app.kicad.catalog import Catalog
from app.kicad.library import index_repository
from app.kicad.parts import load_parts
from app.kicad.sexpr import loads
from tests.test_library_index import footprint, symbol

PART = """\
ipn: 1102-0001
description: Resistor 10k 1% 0603
status: approved
symbol: Passives:R_0603
footprint: Passives:R_0603_1608Metric
fields:
  Value: 10k
mpns:
  - mpn: RC0603FR-0710KL
    preferred: true
"""

MODEL_REF = "${KICAD_LIBRARY_3DMODELS}/Passives.3dshapes/R_0603_1608Metric.step"


def decode(data: str) -> bytes:
    return zstandard.ZstdDecompressor().decompressobj().decompress(base64.b64decode(data))


class Lib:
    def __init__(self, root):
        self.root = root

    def add(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding="utf-8")

    def bundle(self, ipn="1102-0001"):
        cat = Catalog()
        cat.add("company", index_repository(self.root))
        cat.check_references()
        parts = load_parts(self.root)
        return build_assets(parts.parts[ipn], cat, parts, "https://kicad.example.com")


@pytest.fixture()
def lib(tmp_path):
    library = Lib(tmp_path)
    library.add("categories.yaml", '- code: "1102"\n  name: Resistors\n')
    library.add(
        "mpns/RC0603FR-0710KL.yaml",
        "mpn: RC0603FR-0710KL\nmanufacturer: Yageo\nlifecycle: active\n",
    )
    library.add("parts/1102/1102-0001.yaml", PART)
    library.add("symbols/Passives.kicad_symdir/R.kicad_sym", symbol("R", units=2))
    library.add(
        "symbols/Passives.kicad_symdir/R_0603.kicad_sym",
        symbol("R_0603", extends="R", footprint="Passives:R_0603_1608Metric"),
    )
    library.add(
        "footprints/Passives.pretty/R_0603_1608Metric.kicad_mod",
        footprint("R_0603_1608Metric", model=MODEL_REF),
    )
    library.add("3dmodels/Passives.3dshapes/R_0603_1608Metric.step", "ISO-10303-21;\n" * 20)
    return library


def test_round_trip_through_zstd_and_base64():
    raw = b'(footprint "R_0603")\n' * 50
    assert decode(encode(raw)) == raw


def test_symbol_is_sent_last_and_is_the_only_placement(lib):
    """Saving referenced assets first means the symbol lands complete."""
    bundle = lib.bundle()

    commands = [a.command for a in bundle.assets]
    modes = [a.parameters["mode"] for a in bundle.assets]

    assert commands == ["DL_FOOTPRINT", "DL_3DMODEL", "DL_SYMBOL"]
    assert modes == ["SAVE", "SAVE", "PLACE"]
    assert modes.count("PLACE") == 1


def test_payloads_decode_back_to_the_original_files(lib):
    bundle = lib.bundle()
    by_command = {a.command: a for a in bundle.assets}

    symbol_text = decode(by_command["DL_SYMBOL"].data).decode("utf-8")
    tree = loads(symbol_text)
    names = [s.atoms()[0] for s in tree.children("symbol")]

    assert names == ["R_0603"], "inheritance is flattened into one symbol"
    assert "1102-0001" in symbol_text
    assert decode(by_command["DL_FOOTPRINT"].data).startswith(b'(footprint "R_0603_1608Metric"')
    assert decode(by_command["DL_3DMODEL"].data).startswith(b"ISO-10303-21;")


def test_rpc_parameters_match_the_protocol(lib):
    bundle = lib.bundle()
    symbol_asset = bundle.assets[-1]

    assert symbol_asset.parameters == {
        "mode": "PLACE",
        "compression": "ZSTD",
        "content_type": "KICAD_SYMBOL_V1",
        "library": "Passives",
        "name": "R_0603",
    }
    assert bundle.assets[0].parameters["content_type"] == "KICAD_FOOTPRINT_V1"
    assert bundle.assets[1].parameters["content_type"] == "KICAD_3D_MODEL_STEP"


def test_a_missing_footprint_warns_but_still_places(lib):
    """A symbol with no land pattern is still more use than nothing."""
    lib.add(
        "parts/1102/1102-0001.yaml", PART.replace("Passives:R_0603_1608Metric", "Passives:Gone")
    )

    bundle = lib.bundle()

    assert [a.command for a in bundle.assets] == ["DL_SYMBOL"]
    assert any("no land pattern" in w for w in bundle.warnings)


def test_an_lfs_pointer_is_refused_rather_than_sent(lib):
    """Sending the stub gives an empty 3D view with no error anywhere."""
    lib.add(
        "3dmodels/Passives.3dshapes/R_0603_1608Metric.step",
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 41234\n",
    )

    bundle = lib.bundle()

    assert "DL_3DMODEL" not in [a.command for a in bundle.assets]
    assert any("LFS pointer" in w for w in bundle.warnings)


def test_a_large_model_warns_about_the_timeout(lib):
    lib.add("3dmodels/Passives.3dshapes/R_0603_1608Metric.step", "x" * (3 * 1024 * 1024))

    bundle = lib.bundle()

    assert any("response timeout" in w for w in bundle.warnings)
    assert "DL_3DMODEL" in [a.command for a in bundle.assets], "still sent; the user decides"


def test_a_broken_symbol_refuses_the_whole_bundle(lib):
    lib.add(
        "symbols/Passives.kicad_symdir/R_0603.kicad_sym",
        symbol("R_0603", extends="Missing", footprint="Passives:R_0603_1608Metric"),
    )

    with pytest.raises(BuildError):
        lib.bundle()


def test_compression_actually_helps(lib):
    """Payloads cross a WebView string bridge, so wire size matters."""
    bundle = lib.bundle()
    symbol_asset = bundle.assets[-1]

    assert len(symbol_asset.data) < symbol_asset.size_bytes
