"""Indexing a library repository and resolving references between assets."""

import pytest

from app.kicad.library import Severity, index_repository, normalise_model_ref

HEAD = "(kicad_symbol_lib\n" "\t(version 20251024)\n" '\t(generator "kicad_symbol_editor")\n'


def symbol(name, extends=None, footprint="", units=0):
    """A symbol file.

    `units` follows KiCad's convention: unit 0 carries the body shared by all
    units, unit 1 onwards carry the pins.
    """
    lines = [HEAD, f'\t(symbol "{name}"']
    if extends:
        lines.append(f'\t\t(extends "{extends}")')
    lines.append(f'\t\t(property "Value" "{name}")')
    lines.append(f'\t\t(property "Footprint" "{footprint}")')

    for i in range(units):
        lines.append(f'\t\t(symbol "{name}_{i}_1"')
        if i == 0:
            lines += [
                "\t\t\t(rectangle",
                "\t\t\t\t(start -1.016 -2.54)",
                "\t\t\t\t(end 1.016 2.54)",
                "\t\t\t)",
            ]
        else:
            for y, rot, num in (("3.81", "270", "1"), ("-3.81", "90", "2")):
                lines += [
                    "\t\t\t(pin passive line",
                    f"\t\t\t\t(at 0 {y} {rot})",
                    "\t\t\t\t(length 1.27)",
                    f'\t\t\t\t(number "{num}")',
                    "\t\t\t)",
                ]
        lines.append("\t\t)")

    lines += ["\t)", ")", ""]
    return "\n".join(lines)


def footprint(name, model=None):
    """A footprint file with two pads, silkscreen and a courtyard."""
    lines = [f'(footprint "{name}"', '\t(layer "F.Cu")']
    for number, x in (("1", "-0.825"), ("2", "0.825")):
        lines += [
            f'\t(pad "{number}" smd roundrect',
            f"\t\t(at {x} 0)",
            "\t\t(size 0.8 0.95)",
            '\t\t(layers "F.Cu" "F.Mask" "F.Paste")',
            "\t\t(roundrect_rratio 0.25)",
            "\t)",
        ]
    lines += [
        "\t(fp_line",
        "\t\t(start -0.2 -0.5)",
        "\t\t(end 0.2 -0.5)",
        "\t\t(stroke (width 0.12) (type solid))",
        '\t\t(layer "F.SilkS")',
        "\t)",
        "\t(fp_rect",
        "\t\t(start -1.5 -0.7)",
        "\t\t(end 1.5 0.7)",
        "\t\t(stroke (width 0.05) (type solid))",
        "\t\t(fill no)",
        '\t\t(layer "F.CrtYd")',
        "\t)",
    ]
    if model:
        lines += [f'\t(model "{model}"', "\t\t(offset (xyz 0 0 0))", "\t)"]
    lines += [")", ""]
    return "\n".join(lines)


class Repo:
    """A repository laid out on disk for one test."""

    def __init__(self, root):
        self.root = root

    def add(self, relative, text):
        p = self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def index(self):
        return index_repository(self.root)


@pytest.fixture()
def repo(tmp_path):
    return Repo(tmp_path)


def test_indexes_symbols_footprints_and_models(repo):
    repo.add("symbols/Passives.kicad_symdir/R.kicad_sym", symbol("R", units=2))
    repo.add(
        "symbols/Passives.kicad_symdir/R_0603.kicad_sym",
        symbol("R_0603", extends="R", footprint="Passives:R_0603_1608Metric"),
    )
    repo.add(
        "footprints/Passives.pretty/R_0603_1608Metric.kicad_mod",
        footprint(
            "R_0603_1608Metric",
            model="${KICAD_LIBRARY_3DMODELS}/Passives.3dshapes/R_0603_1608Metric.step",
        ),
    )
    repo.add("3dmodels/Passives.3dshapes/R_0603_1608Metric.step", "ISO-10303-21;\n")

    idx = repo.index()

    assert set(idx.symbols) == {("Passives", "R"), ("Passives", "R_0603")}
    assert ("Passives", "R_0603_1608Metric") in idx.footprints
    assert "Passives.3dshapes/R_0603_1608Metric.step" in idx.models
    assert idx.errors == []


def test_resolve_chain_includes_ancestors_child_first(repo):
    repo.add("symbols/L.kicad_symdir/A.kicad_sym", symbol("A", units=2))
    repo.add("symbols/L.kicad_symdir/B.kicad_sym", symbol("B", extends="A"))
    repo.add("symbols/L.kicad_symdir/C.kicad_sym", symbol("C", extends="B"))

    idx = repo.index()
    chain = idx.resolve_chain("L", "C")

    assert [s.name for s in chain] == ["C", "B", "A"]
    assert chain[-1].unit_count == 2, "only the root carries graphics"


def test_missing_parent_is_reported(repo):
    """A derived symbol shipped without its parent places a part with no body."""
    repo.add("symbols/L.kicad_symdir/B.kicad_sym", symbol("B", extends="Nonexistent"))

    idx = repo.index()

    kinds = [i.kind for i in idx.errors]
    assert "missing-parent" in kinds


def test_extends_cycle_is_detected(repo):
    repo.add("symbols/L.kicad_symdir/A.kicad_sym", symbol("A", extends="B"))
    repo.add("symbols/L.kicad_symdir/B.kicad_sym", symbol("B", extends="A"))

    idx = repo.index()
    with pytest.raises(ValueError, match="cycle"):
        idx.resolve_chain("L", "A")


def test_footprint_and_model_refs_are_not_resolved_per_repository(repo):
    """Cross-repository references are the catalog's job, not the indexer's.

    KiCad's own libraries keep symbols, footprints and models in three separate
    repositories, so a reference dangling here may resolve against another
    source. Deciding that requires seeing all of them.
    """
    repo.add("symbols/L.kicad_symdir/R.kicad_sym", symbol("R", footprint="Other:Nope"))
    repo.add(
        "footprints/L.pretty/F.kicad_mod", footprint("F", model="${X}/Other.3dshapes/absent.step")
    )

    idx = repo.index()
    kinds = {i.kind for i in idx.issues}

    assert "missing-footprint" not in kinds
    assert "missing-model" not in kinds


def test_unqualified_footprint_is_a_warning_not_an_error(repo):
    repo.add("symbols/L.kicad_symdir/R.kicad_sym", symbol("R", footprint="R_0603"))

    idx = repo.index()

    assert [i.kind for i in idx.warnings] == ["unqualified-footprint"]
    assert idx.errors == []


def test_name_mismatch_is_reported(repo):
    repo.add("symbols/L.kicad_symdir/Wrong.kicad_sym", symbol("R"))

    idx = repo.index()

    assert any(i.kind == "name-mismatch" for i in idx.warnings)
    assert ("L", "R") in idx.symbols, "still indexed under its real name"


def test_lfs_pointer_is_an_error_not_a_model(repo):
    """Serving a pointer file gives an empty 3D view with no error anywhere."""
    repo.add(
        "3dmodels/L.3dshapes/R.step",
        "version https://git-lfs.github.com/spec/v1\n" "oid sha256:4d7a2...\nsize 41234\n",
    )

    idx = repo.index()

    assert idx.models["L.3dshapes/R.step"].is_lfs_pointer
    assert [i.kind for i in idx.errors] == ["lfs-pointer"]


def test_unparseable_file_does_not_stop_the_walk(repo):
    repo.add("symbols/L.kicad_symdir/broken.kicad_sym", "(kicad_symbol_lib")
    repo.add("symbols/L.kicad_symdir/R.kicad_sym", symbol("R"))

    idx = repo.index()

    assert ("L", "R") in idx.symbols
    assert any(i.kind == "unparseable" for i in idx.errors)


def test_flat_layout_is_supported(repo):
    """KiCad's own libraries put the directories at the repository root."""
    repo.add("Device.kicad_symdir/R.kicad_sym", symbol("R"))

    idx = repo.index()

    assert ("Device", "R") in idx.symbols


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("${KICAD10_3DMODEL_DIR}/L.3dshapes/R.step", "L.3dshapes/R.step"),
        ("${KIPRJMOD}/L.3dshapes/R.step", "L.3dshapes/R.step"),
        ("${ANY_CUSTOM_VAR}/L.3dshapes/R.step", "L.3dshapes/R.step"),
        ("L.3dshapes/R.step", "L.3dshapes/R.step"),
        ("./L.3dshapes/R.step", "L.3dshapes/R.step"),
        (r"${VAR}\L.3dshapes\R.step", "L.3dshapes/R.step"),
    ],
)
def test_model_reference_normalisation(raw, expected):
    """The variable differs per contributor, so only the suffix is meaningful."""
    assert normalise_model_ref(raw) == expected


def test_duplicate_symbol_is_reported(repo):
    repo.add("symbols/L.kicad_symdir/R.kicad_sym", symbol("R"))
    repo.add("symbols/L.kicad_symdir/R2.kicad_sym", symbol("R"))

    idx = repo.index()

    assert any(i.kind == "duplicate-symbol" for i in idx.errors)


def test_issue_renders_with_its_path(repo):
    repo.add("symbols/L.kicad_symdir/B.kicad_sym", symbol("B", extends="Gone"))

    idx = repo.index()
    text = str(idx.errors[0])

    assert text.startswith("error: ")
    assert "symbols/L.kicad_symdir/B.kicad_sym" in text
    assert Severity.ERROR.value == "error"
