"""Reading a KiCad BOM, keeping it, and setting it against the library."""

import csv
import io

import pytest

from app.boms.parse import BomError, compact_references, parse_bom
from app.boms.report import build_report
from app.boms.store import BomExists, BomStore, StoreUnavailable
from tests.support import MPN, PART

PAGE = "https://library.example.com/ipn"

# As KiCad's schematic editor writes it with its default columns: grouped by
# value, with the Datasheet field and no IPN.
KICAD_DEFAULT = f""""Reference","Value","Datasheet","Footprint","Qty","DNP"
"C1,C2,C3","100n","{PAGE}/1101-0001","remote_passives:C_0603_1608Metric","3",""
"R1,R2,R3,R4","10k","{PAGE}/1102-0001","remote_passives:R_0603_1608Metric","4",""
"R7","10k","{PAGE}/1102-0001","remote_passives:R_0603_1608Metric","1","DNP"
"H1,H2","MountingHole","~","MountingHole:MountingHole_3.2mm_M3","2",""
"""


def line_for(parsed, ipn, dnp=False):
    return next(line for line in parsed.lines if line.ipn == ipn and line.dnp is dnp)


# -- reading ----------------------------------------------------------------


def test_kicad_s_default_export_is_read_without_an_ipn_column():
    """Placed symbols carry the part's page in Datasheet, and it ends in the
    IPN -- so the columns KiCad exports unasked are enough."""
    parsed = parse_bom(KICAD_DEFAULT)

    resistor = line_for(parsed, "1102-0001")
    assert resistor.references == ("R1", "R2", "R3", "R4")
    assert resistor.quantity == 4
    assert resistor.value == "10k"
    assert resistor.footprint == "remote_passives:R_0603_1608Metric"
    assert parsed.warnings == ()


def test_an_ipn_column_is_used_when_there_is_one():
    parsed = parse_bom(
        '"Reference","IPN","Datasheet"\n"R1","1102-0001","https://x/ipn/9999-0000"\n'
    )
    assert parsed.lines[0].ipn == "1102-0001", "the field itself, before the address"


def test_what_is_not_a_library_part_is_kept_without_a_number():
    """Mounting holes and test points are on the board too."""
    holes = line_for(parse_bom(KICAD_DEFAULT), None)

    assert holes.references == ("H1", "H2")
    assert holes.value == "MountingHole"


def test_a_part_that_is_not_fitted_is_its_own_line():
    parsed = parse_bom(KICAD_DEFAULT)

    assert line_for(parsed, "1102-0001", dnp=True).references == ("R7",)
    assert parsed.component_count == 9, "3 + 4 + 2; the one not fitted is not counted"


def test_rows_for_the_same_part_are_merged():
    """So it does not matter whether KiCad grouped them."""
    ungrouped = parse_bom(
        "Reference,IPN\nR2,1102-0001\nC1,1101-0001\nR1,1102-0001\nR10,1102-0001\n"
    )

    assert [(line.ipn, line.references) for line in ungrouped.lines] == [
        ("1101-0001", ("C1",)),
        ("1102-0001", ("R1", "R2", "R10")),
    ]


def test_ranges_of_references_are_opened_out():
    parsed = parse_bom("Reference,IPN\nR1-R4,1102-0001\nC1-3,1101-0001\n")

    assert line_for(parsed, "1102-0001").references == ("R1", "R2", "R3", "R4")
    assert line_for(parsed, "1101-0001").quantity == 3


@pytest.mark.parametrize("delimiter", [",", ";", "\t"], ids=["comma", "semicolon", "tab"])
def test_the_delimiter_is_whatever_the_file_uses(delimiter):
    """KiCad lets the user choose, and a German Excel chooses semicolons."""
    text = delimiter.join(["Reference", "Value", "IPN"]) + "\n"
    text += delimiter.join(['"R1 R2"', "10k", "1102-0001"]) + "\n"

    (line,) = parse_bom(text).lines

    assert (line.ipn, line.references, line.value) == ("1102-0001", ("R1", "R2"), "10k")


def test_headings_are_found_whatever_their_case_and_order():
    parsed = parse_bom("﻿QTY, ipn ,DESIGNATOR\n2,1102-0001,R1 R2\n")
    assert parsed.lines[0].quantity == 2


def test_a_quantity_that_disagrees_with_the_references_is_said():
    parsed = parse_bom('Reference,Qty,IPN\n"R1,R2",5,1102-0001\n')

    assert parsed.lines[0].quantity == 2, "the references are what is on the board"
    assert "Row 2 says 5 but lists 2 references" in parsed.warnings[0]


def test_a_reference_listed_twice_is_counted_once():
    parsed = parse_bom("Reference,IPN\nR1,1102-0001\nR1,1102-0001\n")

    assert parsed.lines[0].references == ("R1",)
    assert parsed.lines[0].quantity == 1
    assert "more than once" in parsed.warnings[0]


def test_lines_come_out_in_the_order_of_the_board():
    parsed = parse_bom("Reference,IPN\nU1,1301-0001\nR10,1102-0002\nR2,1102-0001\nC1,1101-0001\n")
    assert [line.references[0] for line in parsed.lines] == ["C1", "R2", "R10", "U1"]


@pytest.mark.parametrize(
    "text,reason",
    [
        ("", "empty"),
        ("   \n\n", "empty"),
        ("Value,Footprint\n10k,R_0603\n", 'no "Reference" column'),
        ("Reference,Value\nR1,10k\n", 'neither an "IPN" nor a "Datasheet" column'),
        ("Reference,IPN\n", "no parts under it"),
    ],
    ids=["empty", "blank", "no-references", "no-part-number", "only-headings"],
)
def test_a_file_that_is_not_a_bom_says_what_to_change(text, reason):
    with pytest.raises(BomError, match=reason):
        parse_bom(text)


def test_references_are_written_the_way_a_person_would():
    assert (
        compact_references(["R10", "R1", "R2", "R3", "C1", "C2", "R5"]) == "C1, C2, R1-R3, R5, R10"
    )
    assert compact_references(["U1A", "U1B", "TP", "J1"]) == "J1, TP, U1A, U1B"
    assert compact_references([]) == ""


# -- keeping ----------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    return BomStore(tmp_path / "boms.db")


def save(store, project="Amplifier", revision="A", text=KICAD_DEFAULT, **kwargs):
    return store.save(parse_bom(text), project=project, revision=revision, source=text, **kwargs)


def test_a_bom_is_stored_and_read_back(store):
    saved = save(store, filename="amplifier.csv", note="first build", uploaded_by="Ada")

    bom = store.get(saved.id)

    assert (bom.project, bom.revision, bom.label) == ("Amplifier", "A", "Amplifier revision A")
    assert (bom.filename, bom.note, bom.uploaded_by) == ("amplifier.csv", "first build", "Ada")
    assert (bom.line_count, bom.component_count) == (4, 9)
    assert bom.lines == parse_bom(KICAD_DEFAULT).lines, "exactly what was read from the file"
    assert bom.uploaded_at > 0
    assert store.source(saved.id) == KICAD_DEFAULT, "and the file itself, whole"


def test_a_stored_bom_survives_a_restart(store, tmp_path):
    saved = save(store)
    assert BomStore(tmp_path / "boms.db").get(saved.id).project == "Amplifier"


def test_boms_are_listed_by_project(store):
    save(store, "Zeta", "A")
    save(store, "amplifier", "A")
    save(store, "amplifier", "B")

    listed = [(bom.project, bom.revision) for bom in store.list()]

    assert listed == [("amplifier", "B"), ("amplifier", "A"), ("Zeta", "A")]
    assert all(bom.lines == () for bom in store.list()), "a list does not load every line"


def test_the_same_revision_twice_has_to_be_chosen(store):
    """Overwriting a record should not be something that just happens."""
    first = save(store)

    with pytest.raises(BomExists) as refused:
        save(store, text="Reference,IPN\nR1,1102-0001\n")
    assert refused.value.existing.id == first.id
    assert store.get(first.id).line_count == 4, "untouched"

    replaced = save(store, text="Reference,IPN\nR1,1102-0001\n", replace_existing=True)
    assert [bom.id for bom in store.list()] == [replaced.id]
    assert store.get(replaced.id).line_count == 1
    assert store.where_used("1101-0001") == [], "the old lines went with it"


def test_a_project_without_a_revision_is_its_own_bom(store):
    save(store, "Amplifier", "")
    save(store, "Amplifier", "A")

    assert [bom.label for bom in store.list()] == ["Amplifier revision A", "Amplifier"]


def test_deleting_a_bom_takes_its_lines(store):
    saved = save(store)

    assert store.delete(saved.id) is True
    assert store.get(saved.id) is None
    assert store.where_used("1102-0001") == []
    assert store.delete(saved.id) is False, "there is nothing left to delete"


def test_where_a_part_is_used(store):
    """What decides whether a part can be changed or retired."""
    old = save(store, "Amplifier", "A")
    new = save(store, "Amplifier", "B", text="Reference,IPN\nR1 R2,1102-0001\n")
    save(store, "Filter", "A", text="Reference,IPN\nC1,1101-0001\n")

    usages = store.where_used("1102-0001")

    assert [(u.bom.id, u.quantity, u.references, u.dnp) for u in usages] == [
        (new.id, 2, ("R1", "R2"), False),
        (old.id, 4, ("R1", "R2", "R3", "R4"), False),
        (old.id, 1, ("R7",), True),
    ]
    assert usages[0].bom.label == "Amplifier revision B"
    assert store.where_used("9999-0000") == []


def test_a_store_that_cannot_be_opened_says_so_and_creates_nothing(tmp_path):
    """A path that points nowhere is a mistake to report, not a reason to
    start making directories wherever a setting happens to point."""
    store = BomStore(tmp_path / "missing" / "boms.db")

    assert "does not exist" in store.problem()
    with pytest.raises(StoreUnavailable):
        store.list()
    assert not (tmp_path / "missing").exists()
    assert BomStore(tmp_path / "boms.db").problem() is None


# -- against the library ----------------------------------------------------


@pytest.fixture()
def report(tree, store):
    """The BOM above, against a library that only half knows it."""
    tree.standard()  # has 1102-0001, and no 1101-0001
    return build_report(store.get(save(store).id), tree.snapshot())


def row_for(report, ipn, dnp=False):
    return next(r for r in report.lines if r.line.ipn == ipn and r.line.dnp is dnp)


def test_each_line_is_looked_up_in_the_library(report):
    resistor = row_for(report, "1102-0001")

    assert resistor.description == "Resistor 10k 1% 0.1W 0603"
    assert resistor.preferred_mpn == "RC0603FR-0710KL"
    assert resistor.source.manufacturer == "Yageo"
    assert resistor.remarks == () and resistor.level is None


def test_what_the_library_does_not_know_is_remarked_on(report):
    unknown, holes = row_for(report, "1101-0001"), row_for(report, None)

    assert [(r.level, r.text) for r in unknown.remarks] == [("bad", "not in the library")]
    assert [(r.level, r.text) for r in holes.remarks] == [("warn", "no part number")]
    assert (report.part_count, report.problems, report.cautions) == (1, 1, 1)


@pytest.mark.parametrize(
    "part,mpn,expected",
    [
        (PART.replace("approved", "obsolete"), MPN, [("bad", "obsolete part")]),
        (PART.replace("approved", "draft"), MPN, [("warn", "draft part")]),
        (PART, MPN.replace("active", "obsolete"), [("bad", "source obsolete")]),
        (PART, MPN.replace("active", "nrnd"), [("warn", "source not for new designs")]),
        (PART.replace("RC0603FR-0710KL", "GHOST"), MPN, [("warn", "source not in the library")]),
        (
            "ipn: 1102-0001\ndescription: R\nsymbol: Passives:R\n",
            MPN,
            [("warn", "draft part"), ("warn", "no source")],
        ),
    ],
    ids=[
        "obsolete-part",
        "draft-part",
        "obsolete-source",
        "nrnd-source",
        "unknown-source",
        "no-source",
    ],
)
def test_a_part_that_should_not_be_bought_as_is_is_remarked_on(tree, store, part, mpn, expected):
    """Looked up now, not when the BOM was uploaded: a part made obsolete
    last week shows on a BOM from last year."""
    saved = save(store, text="Reference,IPN\nR1,1102-0001\n")
    tree.standard()
    tree.write("parts/1102/1102-0001.yaml", part)
    tree.write("mpns/RC0603FR-0710KL.yaml", mpn)

    (row,) = build_report(store.get(saved.id), tree.snapshot()).lines

    assert [(r.level, r.text) for r in row.remarks] == expected


def test_the_other_approved_sources_are_listed(tree, store):
    saved = save(store, text="Reference,IPN\nR1,1102-0001\n")
    tree.standard()
    tree.write("mpns/SECOND.yaml", "mpn: SECOND\nmanufacturer: Vishay\nlifecycle: active\n")
    tree.write("parts/1102/1102-0001.yaml", PART + "  - mpn: SECOND\n")

    (row,) = build_report(store.get(saved.id), tree.snapshot()).lines

    assert row.preferred_mpn == "RC0603FR-0710KL"
    assert row.alternates == ("SECOND",)


def test_the_export_adds_the_library_s_columns(report):
    rows = list(csv.DictReader(io.StringIO(report.csv())))

    assert [row["Item"] for row in rows] == ["1", "2", "3", "4"]
    resistor = next(row for row in rows if row["IPN"] == "1102-0001" and not row["DNP"])
    assert resistor == {
        "Item": "3",
        "References": "R1, R2, R3, R4",
        "Qty": "4",
        "DNP": "",
        "IPN": "1102-0001",
        "Description": "Resistor 10k 1% 0.1W 0603",
        "Value": "10k",
        "Footprint": "remote_passives:R_0603_1608Metric",
        "Status": "approved",
        "MPN": "RC0603FR-0710KL",
        "Manufacturer": "Yageo",
        "Lifecycle": "active",
        "Alternates": "",
        "Datasheet": "https://example.com/yageo.pdf",
        "Remarks": "",
    }
    assert next(row for row in rows if row["DNP"])["References"] == "R7"
    assert next(row for row in rows if row["IPN"] == "1101-0001")["Remarks"] == "not in the library"


def test_the_export_cannot_run_a_formula_in_a_spreadsheet(tree, store):
    """It is opened in Excel, and its cells come out of files and uploads."""
    saved = save(store, text='Reference,Value,IPN\nR1,"=HYPERLINK(""http://evil"")",\nR2,-40,\n')
    tree.standard()

    rows = list(
        csv.DictReader(io.StringIO(build_report(store.get(saved.id), tree.snapshot()).csv()))
    )

    assert rows[0]["Value"] == '\'=HYPERLINK("http://evil")'
    assert rows[1]["Value"] == "-40", "a negative number is not a formula"
