"""What KiCad is sent when a part is placed, and how the part is drawn."""

import base64

import pytest
import zstandard

from app.kicad.sexpr import dumps, loads
from app.kicad.symbol import properties
from app.library import previews
from app.library.placement import (
    PlacementError,
    PlacementOptions,
    asset_bundle,
    encode,
    placed_symbol,
)
from tests.support import MODEL_PATH, PART, symbol_file

OPTIONS = PlacementOptions(public_url="https://kicad.example.com")


def decode(data: str) -> bytes:
    return zstandard.ZstdDecompressor().decompressobj().decompress(base64.b64decode(data))


@pytest.fixture()
def library(tree):
    return tree.standard(model=True)


def place(tree, ipn="1102-0001", options=OPTIONS):
    snapshot = tree.snapshot()
    return placed_symbol(snapshot.part(ipn), snapshot, options)


def bundle(tree, ipn="1102-0001"):
    snapshot = tree.snapshot()
    return asset_bundle(snapshot.part(ipn), snapshot, OPTIONS)


# -- the symbol -------------------------------------------------------------


def test_the_payload_is_one_self_contained_symbol(library):
    """KiCad saves a single symbol per payload, so the ancestor is merged in
    rather than sent alongside."""
    document = loads(place(library).text)
    (symbol,) = document.children("symbol")

    assert symbol.arg(0) == "R_0603"
    assert symbol.child("extends") is None
    assert [unit.arg(0) for unit in symbol.children("symbol")] == ["R_0603_1_1"]
    assert len(list(symbol.descendants("pin"))) == 2
    assert len(list(symbol.descendants("rectangle"))) == 1


def test_it_carries_the_part_and_not_the_template(library):
    fields = properties(place(library).node)

    assert fields["IPN"] == "1102-0001"
    assert fields["Value"] == "10k"
    assert fields["Tolerance"] == "1%"
    assert fields["Description"] == "Resistor 10k 1% 0.1W 0603"
    assert fields["MPN"] == "RC0603FR-0710KL"
    assert fields["Manufacturer"] == "Yageo"
    assert fields["Reference"] == "R", "what the library set is kept"


def test_the_datasheet_is_the_part_s_page(library):
    """With several approved sources there is no one manufacturer's PDF to
    point at. The page lists them all."""
    options = PlacementOptions(public_url="https://kicad.example.com/prefix/")
    fields = properties(place(library, options=options).node)

    assert fields["Datasheet"] == "https://kicad.example.com/prefix/ipn/1102-0001"


def test_the_footprint_names_the_library_kicad_will_file_it_in(library):
    """KiCad saves received footprints under <prefix>_<library>. A symbol
    naming the source library points at one the user does not have, and
    "Update PCB from Schematic" fails with footprint not found -- even though
    the footprint was sent."""
    assert properties(place(library).node)["Footprint"] == "remote_passives:R_0603_1608Metric"


def test_the_prefix_follows_kicad_s_setting(library):
    options = PlacementOptions(public_url="https://x", remote_prefix="corp")
    fields = properties(place(library, options=options).node)

    assert fields["Footprint"] == "corp_passives:R_0603_1608Metric"


@pytest.mark.parametrize(
    "library_name,expected",
    [
        ("Passives", "remote_passives"),
        ("My Library", "remote_my_library"),
        ("Connector_PinHeader_2.54mm", "remote_connector_pinheader_2_54mm"),
        ("Already-fine_1", "remote_already-fine_1"),
    ],
)
def test_library_names_are_sanitised_the_way_kicad_does(library_name, expected):
    assert OPTIONS.remote_library(library_name) == expected


def test_existing_fields_keep_their_place_and_new_ones_are_hidden(library):
    symbol = place(library).node
    by_name = {prop.arg(0): prop for prop in symbol.children("property")}

    assert by_name["Value"].child("at").args == ["0", "0", "90"], "edited where it was"
    assert by_name["Value"].child("hide") is None
    assert by_name["IPN"].child("hide").args == ["yes"], "for the BOM, not the sheet"


def test_the_text_is_what_kicad_would_write(library):
    text = place(library).text

    assert text.startswith("(kicad_symbol_lib\n\t(version 20251024)\n")
    assert dumps(loads(text)) == text


def test_a_symbol_with_no_ancestors_is_placed_as_it_is(library):
    library.part("1102-0002", PART.replace("Passives:R_0603", "Passives:R"))

    symbol = place(library, "1102-0002").node

    assert symbol.arg(0) == "R"
    assert properties(symbol)["IPN"] == "1102-0002"


def test_a_part_with_no_sources_names_no_manufacturer(library):
    library.part("1102-0002", "ipn: 1102-0002\ndescription: Resistor\nsymbol: Passives:R\n")

    fields = properties(place(library, "1102-0002").node)

    assert "MPN" not in fields and "Manufacturer" not in fields


@pytest.mark.parametrize(
    "symbol,message",
    [
        ("''", "names no usable symbol"),
        ("R_0603", "names no usable symbol"),
        ("Passives:Ghost", "which no source provides"),
    ],
)
def test_a_part_without_a_usable_symbol_is_refused(library, symbol, message):
    library.part("1102-0002", PART.replace("symbol: Passives:R_0603", f"symbol: {symbol}"))

    with pytest.raises(PlacementError, match=message):
        place(library, "1102-0002")


def test_a_broken_inheritance_chain_is_refused(library):
    """A clear refusal beats a part that places with no body."""
    library.write(
        "symbols/Passives.kicad_symdir/R_0603.kicad_sym",
        symbol_file("R_0603", extends="Missing", footprint="Passives:R_0603_1608Metric"),
    )

    with pytest.raises(PlacementError, match="would have no body"):
        place(library)


def test_a_refusal_does_not_describe_the_server(library, tmp_path):
    """The message is shown to whoever placed the part."""
    snapshot = library.snapshot()
    # The file changes under the index: a fetch landed, or the disk failed.
    library.write("symbols/Passives.kicad_symdir/R_0603.kicad_sym", "(not a symbol")

    with pytest.raises(PlacementError, match="Passives:R_0603 cannot be read") as refusal:
        placed_symbol(snapshot.part("1102-0001"), snapshot, OPTIONS)

    assert str(tmp_path) not in str(refusal.value)


# -- the bundle -------------------------------------------------------------


def test_the_symbol_goes_last_and_is_the_only_thing_placed(library):
    """Saving what it refers to first means it lands complete."""
    assets = bundle(library).assets

    assert [a.command for a in assets] == ["DL_FOOTPRINT", "DL_3DMODEL", "DL_SYMBOL"]
    assert [a.parameters["mode"] for a in assets] == ["SAVE", "SAVE", "PLACE"]


def test_the_parameters_are_the_protocol_s(library):
    footprint, model, symbol = bundle(library).assets

    assert symbol.parameters == {
        "mode": "PLACE",
        "compression": "ZSTD",
        "content_type": "KICAD_SYMBOL_V1",
        "library": "Passives",
        "name": "R_0603",
    }
    assert (footprint.parameters["content_type"], footprint.parameters["name"]) == (
        "KICAD_FOOTPRINT_V1",
        "R_0603_1608Metric",
    )
    assert (model.parameters["content_type"], model.parameters["name"]) == (
        "KICAD_3D_MODEL_STEP",
        "R_0603_1608Metric.step",
    )
    assert symbol.filename == "R_0603.kicad_sym"


def test_payloads_decode_to_the_files(library):
    footprint, model, symbol = bundle(library).assets

    assert decode(footprint.data).startswith(b'(footprint "R_0603_1608Metric"')
    assert decode(model.data).startswith(b"ISO-10303-21;")
    assert decode(symbol.data).decode("utf-8") == place(library).text
    assert symbol.size_bytes == len(decode(symbol.data))


def test_payloads_are_zstd_then_base64():
    raw = b'(footprint "R_0603")\n' * 50

    assert decode(encode(raw)) == raw
    assert len(encode(raw)) < len(raw), "they cross a string bridge; size matters"


def test_a_missing_footprint_warns_and_still_places(library):
    """A symbol with no land pattern is still more use than nothing."""
    library.part("1102-0002", PART.replace("Passives:R_0603_1608Metric", "Passives:Gone"))

    result = bundle(library, "1102-0002")

    assert [a.command for a in result.assets] == ["DL_SYMBOL"]
    assert any("no land pattern" in warning for warning in result.warnings)


def test_a_part_with_no_footprint_places_quietly(library):
    library.part("1102-0002", "ipn: 1102-0002\ndescription: Test point\nsymbol: Passives:R\n")

    result = bundle(library, "1102-0002")

    assert [a.command for a in result.assets] == ["DL_SYMBOL"]
    assert result.warnings == []


def test_an_lfs_pointer_is_not_sent(library):
    """The stub would give an empty 3D view with no error anywhere."""
    library.write(MODEL_PATH, "version https://git-lfs.github.com/spec/v1\noid sha256:abc\n")

    result = bundle(library)

    assert [a.command for a in result.assets] == ["DL_FOOTPRINT", "DL_SYMBOL"]
    assert any("LFS pointer" in warning for warning in result.warnings)


def test_a_model_nobody_has_warns(library):
    (library.root / MODEL_PATH).unlink()

    result = bundle(library)

    assert "DL_3DMODEL" not in [a.command for a in result.assets]
    assert any("is not available" in warning for warning in result.warnings)


def test_a_large_model_is_sent_with_a_warning(library):
    library.write(MODEL_PATH, "x" * (3 * 1024 * 1024))

    result = bundle(library)

    assert "DL_3DMODEL" in [a.command for a in result.assets], "the user decides"
    assert any("response timeout" in warning for warning in result.warnings)


def test_a_symbol_that_cannot_be_built_refuses_the_whole_bundle(library):
    library.write(
        "symbols/Passives.kicad_symdir/R_0603.kicad_sym",
        symbol_file("R_0603", extends="Missing"),
    )

    with pytest.raises(PlacementError):
        bundle(library)


# -- drawings ---------------------------------------------------------------


def test_the_symbol_preview_is_drawn_from_what_would_be_placed(library):
    snapshot = library.snapshot()
    svg = previews.symbol_svg(snapshot.part("1102-0001"), snapshot, OPTIONS)

    assert svg.startswith("<svg") and 'class="ksym"' in svg
    assert 'data-pin="1"' in svg and 'data-pin="2"' in svg
    assert "<rect" in svg, "the ancestor's body is in it"
    assert "1102-0001 Resistor 10k" in svg


def test_the_footprint_preview_tags_its_pads(library):
    snapshot = library.snapshot()
    svg = previews.footprint_svg(snapshot.part("1102-0001"), snapshot)

    assert 'class="kfp"' in svg
    assert 'data-pad="1"' in svg and 'data-pad="2"' in svg


def test_the_model_is_found_through_the_footprint(library):
    snapshot = library.snapshot()
    assert previews.model_path(snapshot.part("1102-0001"), snapshot) == library.root / MODEL_PATH


def test_what_cannot_be_drawn_is_none_and_not_an_error(library):
    library.part("1102-0002", PART.replace("Passives:R_0603", "Passives:Ghost"))
    library.part("1102-0003", "ipn: 1102-0003\ndescription: Bare\nsymbol: Passives:R\n")
    library.write(MODEL_PATH, "version https://git-lfs.github.com/spec/v1\noid sha256:abc\n")
    snapshot = library.snapshot()
    ghost, bare = snapshot.part("1102-0002"), snapshot.part("1102-0003")

    assert previews.symbol_svg(ghost, snapshot, OPTIONS) is None
    assert previews.footprint_svg(bare, snapshot) is None
    assert previews.model_path(bare, snapshot) is None
    assert previews.model_path(snapshot.part("1102-0001"), snapshot) is None, "a pointer"
