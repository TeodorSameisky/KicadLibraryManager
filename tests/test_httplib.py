"""The library as KiCad's HTTP library, read with personal tokens."""

import json

import pytest

from app.auth.identity import Principal
from app.auth.tokens import MAX_PER_PERSON, TokenStore, TooManyTokens
from app.db.database import Database
from app.library.uploads import UploadedFile, plan_upload, write_upload
from tests.conftest import OPEN, PUBLIC_URL, sign_in, sign_in_librarian
from tests.support import PART, footprint_file, symbol_file

ADA = Principal("user-1", {"sub": "user-1", "name": "Ada Lovelace"})
ROOT = "/kicad-httplib/v1"


# -- the tokens -----------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    now = [1000.0]
    store = TokenStore(Database(tmp_path / "app.db"), clock=lambda: now[0])
    store.now = now
    return store


def test_a_token_reads_as_the_person_who_made_it(store):
    token, secret = store.create(ADA, "  Laptop   at home ")

    assert secret.startswith("klm_") and len(secret) > 40
    assert (token.label, token.owner, token.last_used_at) == (
        "Laptop at home",
        "Ada Lovelace",
        None,
    )
    assert store.principal(secret) == ADA
    assert store.list("user-1")[0].last_used_at == 1000.0, "when it was last used is kept"


def test_only_a_hash_is_kept(store, tmp_path):
    _, secret = store.create(ADA, "Laptop")

    assert secret.encode() not in (tmp_path / "app.db").read_bytes()


def test_what_is_not_a_token_reads_as_nobody(store):
    _, secret = store.create(ADA, "Laptop")

    for wrong in (None, "", secret[:-1], "klm_nothing", secret.removeprefix("klm_")):
        assert store.principal(wrong) is None


def test_a_revoked_token_stops_working_and_only_its_owner_s(store):
    mine, secret = store.create(ADA, "Laptop")
    store.create(Principal("user-2", {"name": "Grace"}), "Lab")

    assert store.revoke(mine.id, "user-2") is False, "not someone else's"
    assert store.revoke(mine.id, "user-1") is True
    assert store.principal(secret) is None
    assert [token.owner for token in store.list()] == ["Grace"]


def test_a_person_s_tokens_are_bounded(store):
    for number in range(MAX_PER_PERSON):
        store.create(ADA, f"{number}")

    with pytest.raises(TooManyTokens):
        store.create(ADA, "one more")


# -- KiCad, with a token -----------------------------------------------------


@pytest.fixture()
def library(make_client, tree):
    tree.standard()
    tree.part("1102-0002", PART.replace("approved", "draft").replace("10k", "22k"))
    tree.part("1102-0003", PART.replace("approved", "obsolete"))
    tree.part("1101-0001", PART.replace("symbol: Passives:R_0603\n", ""))
    return sign_in(make_client(tree))


def kicad(client, secret):
    """KiCad, which has no cookie: only the token from its file."""
    client.cookies.clear()
    client.headers["Authorization"] = f"Token {secret}"
    return client


def test_a_token_is_made_with_the_file_that_holds_it(library):
    made = library.post("/api/v1/tokens", json={"label": "Laptop"})

    assert made.status_code == 201
    body = made.json()
    assert body["token"]["label"] == "Laptop" and body["token"]["yours"] is True
    assert body["library_file"] == {
        "meta": {"version": 1.0},
        "name": "KiCad Library Manager",
        "description": "Parts from KiCad Library Manager",
        "source": {
            "type": "REST_API",
            "api_version": "v1",
            "root_url": f"{PUBLIC_URL}/kicad-httplib",
            "token": body["secret"],
            "timeout_parts_seconds": 60,
            "timeout_categories_seconds": 600,
        },
    }
    listed = library.get("/api/v1/tokens").json()["tokens"]
    assert [token["label"] for token in listed] == ["Laptop"]
    assert "secret" not in json.dumps(listed), "never shown again"


def test_kicad_reads_the_library_with_a_token(library):
    secret = library.post("/api/v1/tokens", json={"label": "Laptop"}).json()["secret"]
    client = kicad(library, secret)

    assert client.get(f"{ROOT}/").json() == {
        "categories": f"{PUBLIC_URL}{ROOT}/categories.json",
        "parts": f"{PUBLIC_URL}{ROOT}/parts",
    }
    assert client.get(f"{ROOT}/categories.json").json() == [
        {"id": "1102", "name": "1102 Resistors, fixed", "description": "Resistors, fixed"}
    ], "1101's only part names no symbol, so there is nothing to offer under it"

    parts = client.get(f"{ROOT}/parts/category/1102.json").json()
    assert [(p["id"], p["description"]) for p in parts] == [
        ("1102-0001", "Resistor 10k 1% 0.1W 0603"),
        ("1102-0002", "Draft: Resistor 22k 1% 0.1W 0603"),
    ], "a retired part is not offered; a draft is, marked"

    part = client.get(f"{ROOT}/parts/1102-0001.json").json()
    assert part == parts[0], "a category's list carries every field, as KiCad needs"
    assert part == {
        "id": "1102-0001",
        "name": "1102-0001",
        "symbolIdStr": "Passives:R_0603",
        "description": "Resistor 10k 1% 0.1W 0603",
        "keywords": "RC0603FR-0710KL Yageo",
        "fields": {
            "IPN": {"value": "1102-0001", "visible": "False"},
            "description": {"value": "Resistor 10k 1% 0.1W 0603", "visible": "False"},
            "datasheet": {"value": f"{PUBLIC_URL}/ipn/1102-0001", "visible": "False"},
            "value": {"value": "10k", "visible": "True"},
            "Tolerance": {"value": "1%", "visible": "False"},
            # The library's own name for it, which a designer has checked out.
            "footprint": {"value": "Passives:R_0603_1608Metric", "visible": "False"},
            "MPN": {"value": "RC0603FR-0710KL", "visible": "False"},
            "Manufacturer": {"value": "Yageo", "visible": "False"},
        },
    }
    assert client.get(f"{ROOT}/parts/1102-0003.json").status_code == 404
    assert client.get(f"{ROOT}/parts/9999-0001.json").status_code == 404


@pytest.mark.parametrize("header", ["", "Token klm_wrong", "Basic abc", "Token "])
def test_without_a_token_that_holds_kicad_is_refused(library, header):
    library.cookies.clear()
    if header:
        library.headers["Authorization"] = header

    assert library.get(f"{ROOT}/categories.json").status_code == 401


def test_a_token_opens_the_http_library_and_nothing_else(library):
    secret = library.post("/api/v1/tokens", json={}).json()["secret"]
    client = kicad(library, secret)

    assert client.get("/api/v1/parts").status_code == 401
    assert client.get("/api/v1/tokens").status_code == 401, "a token cannot make tokens"
    assert client.post("/api/v1/tokens", json={}).status_code == 401


def test_a_revoked_token_is_refused(library):
    made = library.post("/api/v1/tokens", json={"label": "Old laptop"}).json()
    assert library.delete(f"/api/v1/tokens/{made['token']['id']}").json() == {
        "id": made["token"]["id"]
    }

    assert kicad(library, made["secret"]).get(f"{ROOT}/").status_code == 401


def test_a_librarian_revokes_anyone_s_token_and_a_designer_only_their_own(library):
    theirs = library.post("/api/v1/tokens", json={"label": "Ada's"}).json()["token"]["id"]

    sign_in_librarian(library)
    mine = library.post("/api/v1/tokens", json={"label": "Grace's"}).json()["token"]["id"]
    assert {t["owner"] for t in library.get("/api/v1/tokens").json()["tokens"]} == {
        "Ada Lovelace",
        "Grace Hopper",
    }

    sign_in(library)
    assert library.delete(f"/api/v1/tokens/{mine}").status_code == 403
    assert [t["label"] for t in library.get("/api/v1/tokens").json()["tokens"]] == ["Ada's"]

    sign_in_librarian(library)
    assert library.delete(f"/api/v1/tokens/{theirs}").status_code == 200
    assert library.delete(f"/api/v1/tokens/{theirs}").status_code == 404


def test_the_page_offers_a_token_and_lists_them(library):
    library.post("/api/v1/tokens", json={"label": "Laptop"})

    page = library.get("/kicad-library")

    assert page.status_code == 200
    assert 'id="token-form"' in page.text
    assert "Laptop" in page.text
    assert "https://example.com/library.git" in page.text, "what to check out"


def test_with_sign_in_off_the_library_is_open_and_the_file_needs_no_token(make_client, tree):
    client = make_client(tree.standard(), **OPEN)

    assert client.get(f"{ROOT}/categories.json").status_code == 200
    page = client.get("/kicad-library")
    assert 'id="download-open"' in page.text and 'id="token-form"' not in page.text
    assert client.post("/api/v1/tokens", json={}).status_code == 422


# -- the library tables -------------------------------------------------------

SYM_TABLE = """\
(sym_lib_table
  (version 7)
  (lib (name "Passives")(type "KiCad")(uri "${KICAD_LIBRARY_DIR}/symbols/Passives.kicad_symdir")(options "")(descr "Passive components"))
)
"""
FP_TABLE = """\
(fp_lib_table
  (version 7)
  (lib (name "Passives")(type "KiCad")(uri "${KICAD_LIBRARY_DIR}/footprints/Passives.pretty")(options "")(descr ""))
)
"""


def test_a_library_an_upload_begins_is_added_to_the_tables(tree):
    tree.standard()
    tree.write("sym-lib-table", SYM_TABLE)
    tree.write("fp-lib-table", FP_TABLE)
    files = [
        UploadedFile("U.kicad_sym", symbol_file("TPS62840", units=2).encode()),
        UploadedFile("F.kicad_mod", footprint_file("SON-6").encode()),
    ]

    plan = plan_upload("Regulators", files, tree.snapshot())
    write_upload(tree.root, plan)

    symbols = (tree.root / "sym-lib-table").read_text(encoding="utf-8")
    assert symbols == SYM_TABLE.replace(
        "\n)\n",
        '\n  (lib (name "Regulators")(type "KiCad")'
        '(uri "${KICAD_LIBRARY_DIR}/symbols/Regulators.kicad_symdir")(options "")(descr ""))\n)\n',
    )
    assert '(uri "${KICAD_LIBRARY_DIR}/footprints/Regulators.pretty")' in (
        tree.root / "fp-lib-table"
    ).read_text(encoding="utf-8")
    assert "Regulators is added to sym-lib-table." in plan.notes


def test_a_library_the_tables_have_is_left_as_it_is(tree):
    tree.standard()
    tree.write("sym-lib-table", SYM_TABLE)

    plan = plan_upload(
        "Passives",
        [UploadedFile("R.kicad_sym", symbol_file("R_0402", extends="R").encode())],
        tree.snapshot(),
    )

    assert plan.tables == {}


def test_a_repository_without_tables_is_given_none(tree):
    tree.standard()

    plan = plan_upload(
        "Regulators",
        [UploadedFile("F.kicad_mod", footprint_file("SON-6").encode())],
        tree.snapshot(),
    )

    assert plan.tables == {}


def test_a_library_missing_from_a_table_is_an_issue(tree):
    tree.standard()
    tree.write("sym-lib-table", SYM_TABLE)
    tree.write("fp-lib-table", "(fp_lib_table\n  (version 7)\n)\n")

    issues = [i for i in tree.snapshot().issues if i.kind == "library-not-in-table"]

    assert [(i.path, i.message) for i in issues] == [
        (
            "fp-lib-table",
            "the footprint library Passives is not in fp-lib-table, so it is missing for "
            "whoever uses the library checked out",
        )
    ]
