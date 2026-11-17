"""Symbols, footprints and 3D models added from a browser, and taken out again."""

import base64
import shutil

import pytest

from app.kicad.sexpr import loads
from app.library.snapshot import build_snapshot
from app.library.sources import Source
from app.library.uploads import AssetKind, UploadedFile, plan_removal, plan_upload
from tests.conftest import sign_in, sign_in_librarian
from tests.support import MODEL_REF, STEP, LibraryTree, footprint_file, symbol_file
from tests.test_editing import Repository
from tests.test_editing import serve as _serve

HEADER = '(kicad_symbol_lib\n\t(version 20241209)\n\t(generator "vendor")\n'


def symbols(*texts: str) -> str:
    """One .kicad_sym holding several symbols, as a vendor's download does."""
    inner = ["\n".join(text.split("\n")[3:-2]) for text in texts]
    return HEADER + "\n".join(inner) + "\n)\n"


def upload(name: str, text: str) -> UploadedFile:
    return UploadedFile(name, text.encode("utf-8"))


TPS = symbol_file("TPS62840", units=2)
FOOTPRINT = footprint_file("SON-6_1.5x1.5mm", model="${KIPRJMOD}/vendor/models/SON-6.step")
MODEL = UploadedFile("SON-6.step", STEP.encode("ascii"))


@pytest.fixture()
def snapshot(tree):
    return tree.standard(model=True).snapshot()


def by_ref(plan):
    return {item.ref(plan.library): item for item in plan.items}


# -- reading what was uploaded -----------------------------------------------


def test_a_vendor_s_files_are_planned_into_one_library(snapshot):
    plan = plan_upload(
        "Regulators",
        [upload("TPS62840.kicad_sym", TPS), upload("SON-6.kicad_mod", FOOTPRINT), MODEL],
        snapshot,
    )

    assert plan.problems == []
    items = by_ref(plan)
    assert {ref: item.path for ref, item in items.items()} == {
        "Regulators:TPS62840": "symbols/Regulators.kicad_symdir/TPS62840.kicad_sym",
        "Regulators:SON-6_1.5x1.5mm": "footprints/Regulators.pretty/SON-6_1.5x1.5mm.kicad_mod",
        "Regulators.3dshapes/SON-6.step": "3dmodels/Regulators.3dshapes/SON-6.step",
    }
    assert not plan.replaces
    assert items["Regulators:TPS62840"].drawing.startswith("<svg")
    assert items["Regulators:SON-6_1.5x1.5mm"].drawing.startswith("<svg")
    assert items["Regulators.3dshapes/SON-6.step"].data == MODEL.data, "a model is kept as it came"


def test_a_footprint_is_pointed_at_the_model_uploaded_with_it(snapshot):
    """Through the variable the library's own footprints use."""
    plan = plan_upload("Regulators", [upload("SON-6.kicad_mod", FOOTPRINT), MODEL], snapshot)

    footprint = loads(by_ref(plan)["Regulators:SON-6_1.5x1.5mm"].data.decode())
    assert [model.arg(0) for model in footprint.children("model")] == [
        "${KICAD_LIBRARY_3DMODELS}/Regulators.3dshapes/SON-6.step"
    ]
    assert plan.notes == []


def test_a_footprint_with_no_model_is_given_the_one_uploaded_with_it(tree):
    snapshot = tree.standard().snapshot()  # no models: no variable to copy
    bare = footprint_file("SON-6_1.5x1.5mm")

    plan = plan_upload("Regulators", [upload("SON-6.kicad_mod", bare), MODEL], snapshot)

    footprint = loads(by_ref(plan)["Regulators:SON-6_1.5x1.5mm"].data.decode())
    assert [model.arg(0) for model in footprint.children("model")] == [
        "${KICAD10_3DMODEL_DIR}/Regulators.3dshapes/SON-6.step"
    ]
    assert "given SON-6.step at its origin" in plan.notes[0]


def test_a_footprint_that_needs_no_change_is_kept_byte_for_byte(snapshot):
    text = footprint_file("SOT-23")

    plan = plan_upload("Discretes", [upload("SOT-23.kicad_mod", text)], snapshot)

    assert by_ref(plan)["Discretes:SOT-23"].data == text.encode()


def test_a_model_the_library_lacks_is_said_and_not_refused(snapshot):
    plan = plan_upload("Regulators", [upload("SON-6.kicad_mod", FOOTPRINT)], snapshot)

    assert plan.problems == []
    assert "names the 3D model ${KIPRJMOD}/vendor/models/SON-6.step" in plan.notes[0]


def test_a_symbol_file_is_split_one_symbol_per_file(snapshot):
    """And a symbol that derives from another is drawn with its parent's body."""
    text = symbols(symbol_file("OPA_BASE", units=2), symbol_file("OPA333", extends="OPA_BASE"))

    plan = plan_upload("Amplifiers", [upload("vendor.kicad_sym", text)], snapshot)

    assert plan.problems == []
    items = by_ref(plan)
    assert set(items) == {"Amplifiers:OPA_BASE", "Amplifiers:OPA333"}
    written = loads(items["Amplifiers:OPA333"].data.decode())
    assert written.head == "kicad_symbol_lib"
    assert written.child("version").arg(0) == "20241209", "read by the rules it was written to"
    assert [node.arg(0) for node in written.children("symbol")] == ["OPA333"]
    assert items["Amplifiers:OPA333"].drawing.startswith("<svg")


def test_a_symbol_may_derive_from_one_the_library_has(snapshot):
    """R is in Passives already."""
    plan = plan_upload(
        "Passives", [upload("R_0402.kicad_sym", symbol_file("R_0402", extends="R"))], snapshot
    )

    assert plan.problems == []
    assert by_ref(plan)["Passives:R_0402"].drawing is not None


@pytest.mark.parametrize(
    "library,files,expected",
    [
        ("", [upload("a.kicad_mod", footprint_file("A"))], "Name the library"),
        ("../up", [upload("a.kicad_mod", footprint_file("A"))], "Name the library"),
        ("Lib.pretty", [upload("a.kicad_mod", footprint_file("A"))], "Name the library"),
        ("Lib", [], "Choose the files to upload."),
        ("Lib", [upload("old.lib", "EESchema-LIBRARY Version 2.4\n")], "KiCad 5 symbol library"),
        ("Lib", [upload("vendor.zip", "PK")], "Unpack it"),
        ("Lib", [upload("notes.txt", "hello")], "is not a symbol (.kicad_sym)"),
        ("Lib", [upload("a.kicad_mod", '(module "A" (layer F.Cu))')], "KiCad 5's format"),
        ("Lib", [upload("a.kicad_mod", '(footprint "A"')], "cannot be read"),
        ("Lib", [upload("a.kicad_sym", footprint_file("A"))], "is not a KiCad symbol file"),
        ("Lib", [UploadedFile("a.step", b"solid nothing")], "is not a STEP file"),
        ("Lib", [UploadedFile("a.kicad_sym", b"\xff\xfe(")], "is not a text file"),
        (
            "Lib",
            [upload("x.kicad_sym", symbol_file("X", extends="Ghost"))],
            "derives from Ghost, which is not in Lib",
        ),
        (
            "Lib",
            [upload("a.kicad_mod", footprint_file("A/B"))],
            "cannot be a file's name",
        ),
        (
            "Lib",
            [
                upload("a.kicad_mod", footprint_file("A")),
                upload("b.kicad_mod", footprint_file("A")),
            ],
            "The footprint A is uploaded 2 times.",
        ),
    ],
)
def test_what_is_wrong_with_an_upload_is_said(snapshot, library, files, expected):
    plan = plan_upload(library, files, snapshot)

    assert any(expected in problem for problem in plan.problems), plan.problems


def test_what_the_library_has_exactly_is_left_alone(snapshot, tree):
    path = tree.root / "footprints/Passives.pretty/R_0603_1608Metric.kicad_mod"
    same = UploadedFile("R.kicad_mod", path.read_bytes())

    plan = plan_upload(
        "Passives",
        [same, upload("B.kicad_mod", footprint_file("B"))],
        snapshot,
    )

    assert list(by_ref(plan)) == ["Passives:B"]
    assert not plan.replaces
    assert plan.notes == [
        "The footprint Passives:R_0603_1608Metric is in the library exactly as uploaded, "
        "and is left as it is."
    ]
    alone = plan_upload("Passives", [same], snapshot)
    assert alone.problems == ["Everything uploaded is in the library already, exactly as it is."]


def test_what_is_there_is_replaced_and_what_uses_it_is_named(snapshot):
    plan = plan_upload(
        "Passives",
        [upload("R.kicad_mod", footprint_file("R_0603_1608Metric"))],
        snapshot,
    )

    item = by_ref(plan)["Passives:R_0603_1608Metric"]
    assert plan.replaces and item.replaces
    assert item.path == "footprints/Passives.pretty/R_0603_1608Metric.kicad_mod"
    assert item.used_by == ("1102-0001",)


def test_what_another_source_has_cannot_be_uploaded_over(tmp_path):
    ours = LibraryTree(tmp_path / "ours").standard()
    theirs = LibraryTree(
        tmp_path / "theirs", Source(id="kicad", name="KiCad", url="https://example.com/k.git")
    )
    theirs.write("SOT.pretty/SOT-23.kicad_mod", footprint_file("SOT-23"))
    snapshot = build_snapshot([ours.checkout, theirs.checkout])

    plan = plan_upload("SOT", [upload("SOT-23.kicad_mod", footprint_file("SOT-23"))], snapshot)

    assert plan.problems == [
        "The footprint SOT:SOT-23 is already in kicad, which this app does not write to. "
        "Upload it under another library's name."
    ]


def test_a_model_the_repository_keeps_in_lfs_is_refused(tree):
    tree.standard(model=True)
    tree.write(".gitattributes", "*.kicad_mod text\n*.step filter=lfs diff=lfs merge=lfs -text\n")

    plan = plan_upload("Regulators", [upload("SON-6.kicad_mod", FOOTPRINT), MODEL], tree.snapshot())

    assert plan.problems == [
        "This library keeps 3D models like SON-6.step in Git LFS, which this app does not "
        "write. Add the model with git, then upload the footprint alone."
    ]


def test_a_library_kept_at_the_top_is_added_to_at_the_top(tmp_path):
    """The way KiCad's own repositories are laid out."""
    flat = LibraryTree(tmp_path / "flat")
    flat.write("Old.pretty/A.kicad_mod", footprint_file("A"))

    plan = plan_upload("New", [upload("B.kicad_mod", footprint_file("B"))], flat.snapshot())

    assert by_ref(plan)["New:B"].path == "New.pretty/B.kicad_mod"


def test_only_what_nothing_uses_can_be_removed(snapshot):
    removal, problems = plan_removal(AssetKind.FOOTPRINT, "Passives:R_0603_1608Metric", snapshot)
    assert removal is None
    assert problems == ["The footprint Passives:R_0603_1608Metric is used by 1102-0001."]

    removal, problems = plan_removal(AssetKind.SYMBOL, "Passives:R", snapshot)
    assert problems == ["The symbols Passives:R_0603 derive from Passives:R."]

    removal, problems = plan_removal(
        AssetKind.MODEL, "Passives.3dshapes/R_0603_1608Metric.step", snapshot
    )
    assert problems == [
        "The 3D model Passives.3dshapes/R_0603_1608Metric.step is used by "
        "Passives:R_0603_1608Metric."
    ]

    removal, problems = plan_removal(AssetKind.FOOTPRINT, "Passives:Ghost", snapshot)
    assert problems == ["company has no footprint Passives:Ghost."]


# -- as commits, over HTTP ------------------------------------------------------

pytestmark_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def files(*uploads: UploadedFile) -> list[dict]:
    return [
        {"name": each.name, "content": base64.b64encode(each.data).decode("ascii")}
        for each in uploads
    ]


@pytest.fixture()
def repository(tmp_path):
    return Repository(tmp_path)


@pytest.fixture()
def serve(repository, tmp_path, provider):
    """An app over the repository, as the editing tests build it."""
    yield from _serve.__wrapped__(repository, tmp_path, provider)


@pytest.fixture()
def web(serve):
    return sign_in(serve())


@pytestmark_git
def test_a_designer_adds_a_footprint_and_a_model(web, repository):
    body = {
        "library": "Regulators",
        "files": files(upload("SON-6.kicad_mod", FOOTPRINT), MODEL),
        "note": "For the new buck converter",
    }

    checked = web.post("/api/v1/assets/check", json=body)
    assert checked.status_code == 200
    assert checked.json()["problems"] == []
    assert repository.commits() == 1, "checking changes nothing"

    added = web.post("/api/v1/assets", json=body)

    assert added.status_code == 201
    assert repository.last("%an") == "Ada Lovelace"
    assert repository.last("%B").strip() == (
        "Add footprint Regulators:SON-6_1.5x1.5mm, "
        "3D model Regulators.3dshapes/SON-6.step\n\nFor the new buck converter"
    )
    assert "Regulators.3dshapes/SON-6.step" in repository.show(
        "footprints/Regulators.pretty/SON-6_1.5x1.5mm.kicad_mod"
    )
    # Offered to the part form at once.
    choices = web.get("/api/v1/library/choices").json()
    assert "Regulators:SON-6_1.5x1.5mm" in choices["footprints"]


@pytestmark_git
def test_replacing_is_a_librarian_s_and_has_to_be_meant(serve, repository):
    body = {
        "library": "Passives",
        "files": files(upload("R.kicad_mod", footprint_file("R_0603_1608Metric", model=MODEL_REF))),
    }
    client = sign_in(serve())
    refused = client.post("/api/v1/assets", json=body)
    assert refused.status_code == 422
    assert "Only a librarian replaces what is there" in refused.json()["problems"][0]

    librarian = sign_in_librarian(client)
    unmeant = librarian.post("/api/v1/assets", json=body)
    assert unmeant.status_code == 422
    assert unmeant.json()["problems"] == [
        "This would replace Passives:R_0603_1608Metric. Say that it should."
    ]

    replaced = librarian.post("/api/v1/assets", json={**body, "replace": True})
    assert replaced.status_code == 201
    assert repository.last("%s") == "Replace footprint Passives:R_0603_1608Metric"


@pytestmark_git
def test_a_librarian_removes_what_nothing_uses(serve, repository):
    designer = sign_in(serve())
    designer.post(
        "/api/v1/assets",
        json={"library": "Spare", "files": files(upload("A.kicad_mod", footprint_file("A")))},
    )
    assert designer.request("DELETE", "/api/v1/assets/footprint/Spare:A").status_code == 403

    librarian = sign_in_librarian(designer)
    used = librarian.request("DELETE", "/api/v1/assets/footprint/Passives:R_0603_1608Metric")
    assert used.status_code == 422

    removed = librarian.request(
        "DELETE", "/api/v1/assets/footprint/Spare:A", json={"note": "Not needed"}
    )
    assert removed.status_code == 200
    assert repository.last("%s") == "Remove footprint Spare:A"
    assert "Spare:A" not in librarian.get("/api/v1/library/choices").json()["footprints"]


@pytestmark_git
def test_the_pages_show_the_library_and_what_uses_it(serve):
    librarian = sign_in_librarian(serve())

    listing = librarian.get("/library?kind=footprint&q=0603")
    assert listing.status_code == 200
    assert "Passives:R_0603_1608Metric" in listing.text
    assert "1102-0001" in listing.text

    page = librarian.get("/library/footprint/Passives:R_0603_1608Metric")
    assert page.status_code == 200
    assert "<svg" in page.text
    assert "Replace" in page.text
    assert 'id="asset-remove"' not in page.text, "a footprint a part uses is not offered to remove"

    assert librarian.get("/library/symbol/Passives:Ghost").status_code == 404
    assert librarian.get("/library/upload").status_code == 200
    assert librarian.get("/api/v1/assets?kind=symbol").json()["total"] == 2
