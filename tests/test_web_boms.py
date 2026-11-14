"""Uploading a BOM, reading it back, and where a part is used."""

import pytest

from app.boms.store import BomStore
from tests.conftest import OPEN, sign_in, sign_in_librarian
from tests.support import PART
from tests.test_boms import KICAD_DEFAULT


@pytest.fixture()
def api(library_client):
    """Signed in as Ada, with the standard library: it has 1102-0001."""
    return library_client


def upload(client, project="Amplifier", revision="A", content=KICAD_DEFAULT, **extra):
    return client.post(
        "/api/v1/boms",
        json={"project": project, "revision": revision, "content": content, **extra},
    )


# -- uploading --------------------------------------------------------------


def test_a_bom_is_uploaded_and_stored(api):
    response = upload(api, filename="amplifier.csv", note="first build")

    assert response.status_code == 201
    stored = response.json()
    assert stored["project"] == "Amplifier" and stored["revision"] == "A"
    assert (stored["lines"], stored["components"]) == (4, 9)
    assert stored["uploaded_by"] == "Ada Lovelace", "whoever was signed in"
    assert stored["warnings"] == []

    assert api.get("/api/v1/boms").json()["boms"] == [
        {key: value for key, value in stored.items() if key != "warnings"}
    ]


def test_what_the_reader_was_unsure_of_comes_back_with_the_upload(api):
    response = upload(api, content='Reference,Qty,IPN\n"R1,R2",5,1102-0001\n')

    assert response.status_code == 201
    assert "says 5 but lists 2" in response.json()["warnings"][0]


def test_a_file_that_is_not_a_bom_is_refused_with_what_to_change(api):
    response = upload(api, content="Reference,Value\nR1,10k\n")

    assert response.status_code == 422
    assert "Add the IPN field to the BOM export in KiCad" in response.json()["detail"]
    assert api.get("/api/v1/boms").json()["boms"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"project": "", "content": KICAD_DEFAULT},
        {"project": "   ", "content": KICAD_DEFAULT},
        {"project": "Amplifier", "content": ""},
        {"project": "Amplifier"},
        {"project": "x" * 201, "content": KICAD_DEFAULT},
    ],
    ids=["no-project", "blank-project", "empty-file", "no-file", "long-project"],
)
def test_an_upload_needs_a_project_and_a_file(api, body):
    assert api.post("/api/v1/boms", json=body).status_code == 422


def test_names_are_tidied(api):
    stored = upload(api, project="  Power   Amplifier ", revision=" B ").json()
    assert (stored["project"], stored["revision"]) == ("Power Amplifier", "B")


def test_the_same_revision_again_has_to_be_confirmed(api):
    """Replacing a record is something a person chooses."""
    first = upload(api).json()

    refused = upload(api, content="Reference,IPN\nR1,1102-0001\n")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "A BOM for Amplifier revision A is already stored."
    assert refused.json()["existing"]["id"] == first["id"]

    replaced = upload(api, content="Reference,IPN\nR1,1102-0001\n", replace=True)
    assert replaced.status_code == 201
    assert [bom["lines"] for bom in api.get("/api/v1/boms").json()["boms"]] == [1]


# -- reading ----------------------------------------------------------------


def test_a_bom_comes_back_with_every_line_looked_up(api):
    bom_id = upload(api).json()["id"]

    report = api.get(f"/api/v1/boms/{bom_id}").json()

    assert (report["parts"], report["problems"], report["cautions"]) == (1, 1, 1)
    by_refs = {tuple(item["references"]): item for item in report["items"]}
    assert by_refs[("R1", "R2", "R3", "R4")] == {
        "references": ["R1", "R2", "R3", "R4"],
        "quantity": 4,
        "dnp": False,
        "ipn": "1102-0001",
        "value": "10k",
        "footprint": "remote_passives:R_0603_1608Metric",
        "known": True,
        "description": "Resistor 10k 1% 0.1W 0603",
        "status": "approved",
        "mpn": "RC0603FR-0710KL",
        "manufacturer": "Yageo",
        "lifecycle": "active",
        "alternates": [],
        "remarks": [],
    }
    assert by_refs[("C1", "C2", "C3")]["remarks"] == [
        {"level": "bad", "text": "not in the library"}
    ]
    assert by_refs[("R7",)]["dnp"] is True


def test_the_report_follows_the_library_as_it_is_now(make_client, tree):
    """Which is the point of keeping the BOM here."""
    from app.boms.parse import parse_bom

    tree.standard()
    tree.write("parts/1102/1102-0001.yaml", PART.replace("approved", "obsolete"))
    client = sign_in(make_client(tree))
    # Uploaded "last year", when the part was still approved.
    bom = client.app.state.boms.save(parse_bom(KICAD_DEFAULT), project="Old", source=KICAD_DEFAULT)

    items = client.get(f"/api/v1/boms/{bom.id}").json()["items"]

    remarks = [r["text"] for item in items if item["ipn"] == "1102-0001" for r in item["remarks"]]
    assert remarks == ["obsolete part", "obsolete part"]


def test_the_export_is_a_csv_excel_will_read(api):
    bom_id = upload(api, project="Power Amp / Mk2").json()["id"]

    response = api.get(f"/api/v1/boms/{bom_id}/export.csv")

    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert (
        response.headers["content-disposition"] == 'attachment; filename="Power_Amp_Mk2-A-bom.csv"'
    )
    assert response.content.startswith(b"\xef\xbb\xbf"), "or Excel misreads every non-ASCII letter"
    assert "Item,References,Qty,DNP,IPN,Description" in response.text
    assert "RC0603FR-0710KL,Yageo,active" in response.text


def test_the_file_can_be_had_back_as_it_was_uploaded(api):
    bom_id = upload(api).json()["id"]

    response = api.get(f"/api/v1/boms/{bom_id}/source.csv")

    assert response.text.lstrip("﻿") == KICAD_DEFAULT
    assert "as-uploaded.csv" in response.headers["content-disposition"]


def test_only_a_librarian_can_delete_a_bom(api):
    """Anyone may add a record. Throwing one away cannot be undone."""
    bom_id = upload(api).json()["id"]

    refused = api.delete(f"/api/v1/boms/{bom_id}")

    assert refused.status_code == 403
    assert refused.json() == {"detail": "Only a librarian can do this."}
    assert api.get(f"/api/v1/boms/{bom_id}").status_code == 200, "still there"
    assert 'id="bom-delete"' not in api.get(f"/boms/{bom_id}").text, "and not offered"


def test_a_bom_can_be_deleted(api):
    bom_id = upload(api).json()["id"]
    sign_in_librarian(api)
    assert 'id="bom-delete"' in api.get(f"/boms/{bom_id}").text

    assert api.delete(f"/api/v1/boms/{bom_id}").status_code == 204
    assert api.get(f"/api/v1/boms/{bom_id}").status_code == 404
    assert api.delete(f"/api/v1/boms/{bom_id}").status_code == 404
    assert api.get("/api/v1/parts/1102-0001/where-used").json()["used_in"] == []


@pytest.mark.parametrize("suffix", ["", "/export.csv", "/source.csv"])
def test_a_bom_that_does_not_exist_is_404(api, suffix):
    assert api.get(f"/api/v1/boms/999{suffix}").status_code == 404
    assert api.get(f"/api/v1/boms/not-a-number{suffix}").status_code == 422


# -- where used -------------------------------------------------------------


def test_where_a_part_is_used(api):
    upload(api, "Amplifier", "A")
    upload(api, "Filter", "", content="Reference,IPN\nR1 R2,1102-0001\n")

    body = api.get("/api/v1/parts/1102-0001/where-used").json()

    assert body["ipn"] == "1102-0001"
    assert [
        (u["bom"]["project"], u["bom"]["revision"], u["quantity"], u["references"], u["dnp"])
        for u in body["used_in"]
    ] == [
        ("Amplifier", "A", 4, ["R1", "R2", "R3", "R4"], False),
        ("Amplifier", "A", 1, ["R7"], True),
        ("Filter", "", 2, ["R1", "R2"], False),
    ]
    assert api.get("/api/v1/parts/9999-0000/where-used").status_code == 404, "no such part"


def test_a_part_s_page_says_where_it_is_used(api):
    assert "Not on any stored BOM" in api.get("/ipn/1102-0001").text

    bom_id = upload(api).json()["id"]
    text = api.get("/ipn/1102-0001").text

    assert "Where used" in text
    assert f'href="/boms/{bom_id}"' in text and "Amplifier" in text
    assert "R1-R4" in text, "references the way a person writes them"
    assert "not fitted" in text
    assert "Not on any stored BOM" not in text


# -- pages ------------------------------------------------------------------


def test_the_bom_list_page(api):
    empty = api.get("/boms").text
    assert "Nothing has been uploaded yet" in empty
    assert 'id="upload"' in empty and "boms.js" in empty
    assert 'aria-current="page">BOMs<' in empty

    bom_id = upload(api, note="first <build>").json()["id"]
    listed = api.get("/boms").text

    assert f'href="/boms/{bom_id}"' in listed and "Amplifier" in listed
    assert "by Ada Lovelace" in listed
    assert "first &lt;build&gt;" in listed, "what was typed is text, not markup"


def test_a_bom_s_page_shows_it_against_the_library(api):
    bom_id = upload(api, filename="amplifier.csv").json()["id"]

    text = api.get(f"/boms/{bom_id}").text

    assert "Amplifier" in text and "revision A" in text and "amplifier.csv" in text
    assert 'href="/ipn/1102-0001"' in text and "Resistor 10k 1% 0.1W 0603" in text
    assert "R1-R4" in text and "RC0603FR-0710KL" in text and "Yageo" in text
    assert "not in the library" in text and "no part number" in text and "not fitted" in text
    assert "MountingHole" in text, "what is not a library part is still on the list"
    assert 'href="/ipn/1101-0001"' not in text, "no link to a part that does not exist"
    assert f'href="/api/v1/boms/{bom_id}/export.csv"' in text
    assert f'data-bom-id="{bom_id}"' in text


def test_a_bom_page_that_does_not_exist_is_a_page(api):
    response = api.get("/boms/999", headers={"Accept": "text/html"})

    assert response.status_code == 404
    assert "There is no such BOM" in response.text


def test_the_pages_carry_the_path_prefix(make_client, tree):
    client = sign_in(make_client(tree.standard(), PUBLIC_URL="https://sameisky.tech/kicadLibrary"))
    bom_id = upload(client).json()["id"]

    for path in ("/boms", f"/boms/{bom_id}", "/ipn/1102-0001", "/panel"):
        text = client.get(path).text
        assert 'href="/boms' not in text and 'href="/api' not in text, path
        assert 'href="/kicadLibrary/boms' in text, path


# -- who, and when it cannot --------------------------------------------------

CLOSED = [
    ("GET", "/api/v1/boms"),
    ("POST", "/api/v1/boms"),
    ("GET", "/api/v1/boms/1"),
    ("GET", "/api/v1/boms/1/export.csv"),
    ("GET", "/api/v1/boms/1/source.csv"),
    ("DELETE", "/api/v1/boms/1"),
    ("GET", "/api/v1/parts/1102-0001/where-used"),
    ("GET", "/boms"),
    ("GET", "/boms/1"),
]


@pytest.mark.parametrize("method,path", CLOSED)
def test_boms_are_closed_to_someone_signed_out(make_client, tree, method, path):
    client = make_client(tree.standard())
    assert client.request(method, path).status_code == 401


def test_with_sign_in_off_an_upload_has_no_name_on_it(make_client, tree):
    client = make_client(tree.standard(), **OPEN)

    stored = upload(client).json()

    assert stored["uploaded_by"] == ""
    assert client.get("/boms").status_code == 200
    # Nobody can be told apart, on a network declared to be trusted.
    assert client.delete(f"/api/v1/boms/{stored['id']}").status_code == 204


@pytest.fixture()
def without_storage(make_client, tree, tmp_path):
    client = sign_in(make_client(tree.standard()))
    client.app.state.boms = BomStore(tmp_path / "no-such-directory" / "boms.db")
    return client


def test_without_storage_the_api_says_try_later(without_storage, tmp_path):
    for response in (
        upload(without_storage),
        without_storage.get("/api/v1/boms"),
        without_storage.get("/api/v1/boms/1"),
        without_storage.get("/api/v1/parts/1102-0001/where-used"),
    ):
        assert response.status_code == 503
        assert tmp_path.name not in response.text, "and does not say where it looked"


def test_without_storage_the_pages_still_work(without_storage, tmp_path):
    """A part is still worth showing without the list of where it is used --
    and that list must not read as "used nowhere"."""
    part = without_storage.get("/ipn/1102-0001")
    assert part.status_code == 200
    assert "Where used" not in part.text and "Not on any stored BOM" not in part.text

    boms = without_storage.get("/boms")
    assert boms.status_code == 200
    assert "BOMs cannot be stored or read right now" in boms.text

    status = without_storage.get("/")
    assert "unavailable" in status.text
    assert tmp_path.name not in status.text + boms.text, "the status page is open to anyone"


def test_the_status_page_says_storage_is_there(api):
    text = api.get("/").text
    assert "BOM storage" in text and "available" in text and "unavailable" not in text
