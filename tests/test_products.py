"""Product families and devices, and the BOMs attached to them."""

import sqlite3
import textwrap

import pytest

from app.boms.parse import parse_bom
from app.boms.store import BomStore
from app.boms.tree import build_tree
from app.library.products import load_products
from tests.conftest import sign_in
from tests.test_boms import KICAD_DEFAULT

AKD = """\
name: AKD Servodrive
description: Digital servo drives
repository: https://github.com/acme/akd
tags: [servo, drive]
devices:
  - id: mv-3
    name: AKD MV 3
    repository: https://github.com/acme/akd-mv-3
    tags: [mv, 3A]
  - id: hv-6
    name: AKD HV 6
    tags: [hv, 6A]
"""

KED = """\
name: KED Drive
devices:
  - id: mv-3
    name: KED MV 3
"""


def products_tree(tree, **files):
    tree.standard()
    for name, text in files.items():
        tree.write(f"products/{name}.yaml", text)
    return tree


def load(tmp_path, **files):
    for name, text in files.items():
        path = tmp_path / "products" / f"{name}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return load_products(tmp_path)


def bom(store, device="", project="Drive", revision="A", **extra):
    return store.save(
        parse_bom(KICAD_DEFAULT), project=project, revision=revision, device=device, **extra
    )


# -- reading the files --------------------------------------------------------


def test_a_family_and_its_devices_are_read(tmp_path):
    products, issues = load(tmp_path, akd=AKD)

    assert issues == []
    (family,) = products.families
    assert (family.id, family.name, family.description) == (
        "akd",
        "AKD Servodrive",
        "Digital servo drives",
    )
    assert family.tags == ("servo", "drive")
    assert [d.key for d in family.devices] == ["akd/mv-3", "akd/hv-6"]
    assert family.devices[0].repository_url == "https://github.com/acme/akd-mv-3"
    assert products.device("akd/hv-6").name == "AKD HV 6"


def test_families_are_in_alphabetical_order_and_a_device_keeps_the_order_it_was_written_in(
    tmp_path,
):
    products, _ = load(tmp_path, ked=KED, akd=AKD)

    assert [f.name for f in products.families] == ["AKD Servodrive", "KED Drive"]
    assert [d.id for d in products.family("akd").devices] == ["mv-3", "hv-6"]


def test_the_same_device_id_in_two_families_is_two_devices(tmp_path):
    products, issues = load(tmp_path, akd=AKD, ked=KED)

    assert issues == []
    assert products.device("akd/mv-3").name == "AKD MV 3"
    assert products.device("ked/mv-3").name == "KED MV 3"


def test_a_name_is_optional_and_the_id_stands_in(tmp_path):
    products, _ = load(tmp_path, bare="devices:\n  - id: x1\n")

    assert products.family("bare").name == "bare"
    assert products.device("bare/x1").name == "x1"


def test_tags_are_collected_for_a_filter_to_offer(tmp_path):
    products, _ = load(tmp_path, akd=AKD, ked="name: KED\ntags: Servo\ndevices: []\n")

    assert products.tags == ("3A", "6A", "drive", "hv", "mv", "servo")
    assert products.family("akd").all_tags == ("servo", "drive", "mv", "3A", "hv", "6A")


def test_no_products_directory_is_no_products(tmp_path):
    products, issues = load_products(tmp_path)

    assert not products and issues == []


@pytest.mark.parametrize(
    "text,kind,fragment",
    [
        ("name: [", "invalid-yaml", ""),
        ("- a\n- b\n", "bad-product", "expected a mapping"),
        ("devices: nope\n", "bad-product", "'devices' must be a list"),
        ("devices:\n  - just a string\n", "bad-product", "malformed device"),
        ("devices:\n  - {name: no id}\n", "bad-product", "malformed device"),
        ("devices:\n  - id: a/b\n", "bad-product", "letters, digits"),
        ("devices:\n  - id: a\n  - id: a\n", "bad-product", "listed twice"),
        ("tags: {a: 1}\n", "bad-product", "'tags' must be a list"),
    ],
)
def test_what_is_wrong_with_a_file_is_said_and_the_rest_still_loads(tmp_path, text, kind, fragment):
    products, issues = load(tmp_path, bad=text, akd=AKD)

    assert any(i.kind == kind and fragment in i.message for i in issues), issues
    assert all(i.path == "products/bad.yaml" for i in issues)
    assert products.family("akd") is not None, "one file's mistake leaves the others"


def test_a_repository_that_is_not_a_web_address_is_shown_but_not_linked(tmp_path):
    products, issues = load(
        tmp_path,
        evil="repository: 'javascript:alert(1)'\ndevices:\n  - {id: a, repository: ftp://x}\n",
    )

    family = products.family("evil")
    assert family.repository == "javascript:alert(1)" and family.repository_url is None
    assert family.devices[0].repository_url is None
    assert [i.kind for i in issues] == ["repository-not-a-link"] * 2
    assert all(i.severity.value == "warning" for i in issues)


def test_the_library_index_carries_the_products(tree):
    products_tree(tree, akd=AKD)

    snapshot = tree.snapshot()

    assert [f.id for f in snapshot.products.families] == ["akd"]
    assert snapshot.parts_source == "company"


def test_a_problem_in_a_products_file_is_an_issue_of_the_library(tree):
    products_tree(tree, bad="devices: nope\n")

    snapshot = tree.snapshot()

    assert any(i.kind == "bad-product" and i.source == "company" for i in snapshot.issues)


# -- the tree and the BOMs ----------------------------------------------------


def test_each_device_has_its_boms_newest_first(tmp_path):
    products, _ = load(tmp_path, akd=AKD)
    store = BomStore(tmp_path / "boms.db")
    first = bom(store, "akd/mv-3", revision="A")
    second = bom(store, "akd/mv-3", revision="B")
    other = bom(store, "akd/hv-6", revision="A", project="HV")

    tree = build_tree(products, store.list())

    mv, hv = tree.families[0].devices
    assert [b.id for b in mv.boms] == [second.id, first.id]
    assert mv.latest.id == second.id
    assert [b.id for b in hv.boms] == [other.id]
    assert tree.families[0].bom_count == 3 and tree.device_count == 2
    assert tree.unassigned == ()


def test_a_bom_for_nothing_in_the_tree_is_unassigned_not_lost(tmp_path):
    products, _ = load(tmp_path, akd=AKD)
    store = BomStore(tmp_path / "boms.db")
    loose = bom(store, "", project="Loose")
    gone = bom(store, "akd/removed", project="Gone")

    tree = build_tree(products, store.list())

    assert {b.id for b in tree.unassigned} == {loose.id, gone.id}
    assert all(not node.boms for node in tree.families[0].devices)


# -- the store ----------------------------------------------------------------


def test_a_bom_database_from_before_devices_gets_the_column_and_keeps_its_boms(tmp_path):
    path = tmp_path / "old.db"
    store = BomStore(path)
    kept = bom(store, project="Old")
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE boms DROP COLUMN device")
        # Steps were not numbered then either, and it had nothing a later step adds.
        connection.execute("PRAGMA user_version = 0")
        connection.execute("DROP TABLE supplier_answers")
        connection.execute("DROP TABLE supplier_calls")

    reopened = BomStore(path)

    assert reopened.get(kept.id).device == ""
    assert reopened.attach(kept.id, "akd/mv-3") is True
    assert reopened.get(kept.id).device == "akd/mv-3"


def test_attaching_to_a_bom_that_is_not_there_says_so(tmp_path):
    assert BomStore(tmp_path / "boms.db").attach(99, "akd/mv-3") is False


# -- the pages and the API ----------------------------------------------------


@pytest.fixture()
def api(make_client, tree):
    return sign_in(make_client(products_tree(tree, akd=AKD, ked=KED)))


def upload(client, **extra):
    return client.post(
        "/api/v1/boms",
        json={"project": "AKD MV 3", "revision": "A", "content": KICAD_DEFAULT, **extra},
    )


def test_a_bom_is_uploaded_for_a_device(api):
    response = upload(api, device="akd/mv-3")

    assert response.status_code == 201 and response.json()["device"] == "akd/mv-3"
    (listed,) = api.get("/api/v1/boms").json()["boms"]
    assert listed["device"] == "akd/mv-3"


def test_a_device_that_is_not_in_the_products_is_refused(api):
    response = upload(api, device="akd/nope")

    assert response.status_code == 422 and "no device 'akd/nope'" in response.json()["detail"]
    assert api.get("/api/v1/boms").json()["boms"] == []


def test_a_bom_can_be_attached_to_a_device_later_and_taken_off(api):
    stored = upload(api).json()
    assert stored["device"] == ""

    attached = api.put(f"/api/v1/boms/{stored['id']}/device", json={"device": "ked/mv-3"})
    assert attached.status_code == 200
    assert api.get(f"/api/v1/boms/{stored['id']}").json()["device"] == "ked/mv-3"

    api.put(f"/api/v1/boms/{stored['id']}/device", json={"device": ""})
    assert api.get(f"/api/v1/boms/{stored['id']}").json()["device"] == ""


def test_attaching_needs_a_real_device_and_a_real_bom(api):
    stored = upload(api).json()

    assert api.put(f"/api/v1/boms/{stored['id']}/device", json={"device": "x/y"}).status_code == 422
    assert api.put("/api/v1/boms/999/device", json={"device": "akd/mv-3"}).status_code == 404


def test_the_products_api_lists_the_tree_with_its_boms(api):
    stored = upload(api, device="akd/mv-3").json()
    upload(api, project="Spare")

    body = api.get("/api/v1/products").json()

    assert body["tags"] == ["3A", "6A", "drive", "hv", "mv", "servo"]
    akd = body["families"][0]
    assert (akd["id"], akd["repository"]) == ("akd", "https://github.com/acme/akd")
    mv, hv = akd["devices"]
    assert mv["key"] == "akd/mv-3" and [b["id"] for b in mv["boms"]] == [stored["id"]]
    assert hv["boms"] == []
    assert [b["project"] for b in body["unassigned"]] == ["Spare"]


def test_the_products_page_shows_the_tree(api):
    stored = upload(api, device="akd/mv-3").json()

    html = api.get("/products").text

    assert "AKD Servodrive" in html and "KED Drive" in html
    assert 'id="device-akd-mv-3"' in html and 'id="device-ked-mv-3"' in html
    assert f'href="/boms/{stored["id"]}"' in html
    assert 'data-external="The repository"' in html
    assert 'href="https://github.com/acme/akd-mv-3"' in html
    assert ">github.com/acme/akd-mv-3<" in html, "shown without the scheme"
    assert "none yet" in html, "a device with no BOM says so"
    assert 'id="product-tag"' in html and '<option value="servo">servo</option>' in html


def test_a_device_is_searched_by_its_family_s_words_and_tags_too(api):
    html = api.get("/products").text

    row = html.split('id="device-akd-hv-6"', 1)[1].split(">", 1)[0]
    assert "akd servodrive" in row and "hv" in row
    assert 'data-tags="servo|drive|hv|6a"' in row, "the family's tags, then the device's"


def test_a_repository_that_is_not_a_web_address_is_text_on_the_page(make_client, tree):
    products_tree(tree, bad="devices:\n  - {id: a, repository: 'javascript:alert(1)'}\n")
    html = sign_in(make_client(tree)).get("/products").text

    assert "javascript:alert(1)" in html and 'href="javascript' not in html


def test_the_page_says_how_to_start_when_there_are_no_products(library_client):
    html = library_client.get("/products").text

    assert "No products are written down yet" in html and "products/akd.yaml" in html
    assert 'id="product-filter"' not in html


def test_unassigned_boms_are_listed_after_the_tree(api):
    stored = upload(api).json()

    html = api.get("/products").text

    assert "Not on a device" in html
    assert html.index("Not on a device") > html.index("KED Drive")
    assert f'href="/boms/{stored["id"]}"' in html


def test_the_upload_form_offers_the_devices_by_family(api):
    html = api.get("/boms").text

    assert '<optgroup label="AKD Servodrive">' in html
    assert '<option value="akd/mv-3">AKD MV 3</option>' in html
    assert '<option value="ked/mv-3">KED MV 3</option>' in html


def test_the_upload_form_has_no_device_choice_without_products(library_client):
    assert 'id="bom-device"' not in library_client.get("/boms").text


def test_the_boms_page_and_a_bom_say_which_device_it_is_for(api):
    stored = upload(api, device="akd/mv-3").json()

    listed = api.get("/boms").text
    page = api.get(f"/boms/{stored['id']}").text

    assert 'href="/products#device-akd-mv-3"' in listed
    assert "AKD Servodrive &middot; AKD MV 3" in page
    assert '<option value="akd/mv-3" selected>AKD MV 3</option>' in page


def test_a_bom_of_a_device_that_has_gone_says_so(api):
    stored = upload(api).json()
    # What is left when a device is taken out of the products: a key that
    # names nothing now. It cannot be set through the API, which checks.
    api.app.state.boms.attach(stored["id"], "akd/removed")

    page = api.get(f"/boms/{stored['id']}").text

    assert "Made for akd/removed, which is no longer in the products" in page
    assert "Not on a device" in api.get("/products").text


def test_every_page_links_to_the_products(library_client):
    assert 'href="/products"' in library_client.get("/boms").text


def test_the_products_need_a_session(client):
    assert client.get("/api/v1/products").status_code == 401
