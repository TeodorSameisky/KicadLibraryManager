"""Asking suppliers about manufacturer parts: DigiKey, and not asking twice.

DigiKey is stood in for by an httpx transport that answers the way its
Product Information API (v4) does: the token exchange, the keyword search,
and products in the shape real answers have. What is exercised is this app's
own client, and the rules about when a supplier is asked at all.
"""

import json
import sqlite3
import urllib.parse

import httpx
import pytest

from app.config import Settings
from app.db.backup import copy_database
from app.db.database import Database, DatabaseUnavailable
from app.domain.manufacturers import Manufacturer, Manufacturers
from app.domain.supply import Offer, PriceBreak, Query, stands_worse
from app.services.supply import RETRY_AFTER_SECONDS, State, Supply
from app.suppliers.base import SupplierError
from app.suppliers.digikey import DigiKey, lifecycle_of
from app.suppliers.store import KEEP_STALE_SECONDS, SupplierStore
from tests.conftest import ENVIRONMENT

NOBODY = Manufacturers()
DAY = 24 * 3600


def variation(sku, package, minimum, breaks, **more):
    return {
        "DigiKeyProductNumber": sku,
        "PackageType": {"Id": 2, "Name": package},
        "StandardPricing": [
            {"BreakQuantity": quantity, "UnitPrice": price, "TotalPrice": quantity * price}
            for quantity, price in breaks
        ],
        "MyPricing": [],
        "MarketPlace": False,
        "QuantityAvailableforPackageType": 1000,
        "MinimumOrderQuantity": minimum,
        **more,
    }


def product(mpn="RC0603FR-0710KL", maker="YAGEO", status="Active", **more):
    """A product as DigiKey's keyword search returns one."""
    return {
        "Description": {
            "ProductDescription": "RES 10K OHM 1% 1/10W 0603",
            "DetailedDescription": "10 kOhms ±1% 0.1W, 1/10W Chip Resistor 0603",
        },
        "Manufacturer": {"Id": 13, "Name": maker},
        "ManufacturerProductNumber": mpn,
        "UnitPrice": 0.1,
        "ProductUrl": f"https://www.digikey.de/en/products/detail/yageo/{mpn}/726880",
        "DatasheetUrl": "//www.yageo.com/upload/media/datasheet.pdf",
        "ProductVariations": [
            variation(
                "311-10.0KHRTR-ND", "Tape & Reel (TR)", 5000, [(5000, 0.004), (10000, 0.003)]
            ),
            variation(
                "311-10.0KHRCT-ND",
                "Cut Tape (CT)",
                1,
                [(1, 0.1), (10, 0.021), (100, 0.0107), (1000, 0.0061)],
            ),
        ],
        "QuantityAvailable": 7557063,
        "ProductStatus": {"Id": 0, "Status": status},
        "Discontinued": False,
        "EndOfLife": False,
        "ManufacturerLeadWeeks": "18",
        **more,
    }


class FakeDigiKey:
    """DigiKey's API, as far as this app uses it."""

    def __init__(self):
        self.products = [product()]
        self.secret = "s3cret"
        self.tokens = []  # every token handed out
        self.searches = []  # every search made: what was asked for
        self.headers = []  # and the headers it came with
        self.answer = None  # (status, body) to give instead of the products
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("no route to host", request=request)

        if request.url.path == "/v1/oauth2/token":
            sent = dict(urllib.parse.parse_qsl(request.content.decode()))
            if sent != {
                "client_id": "the-client",
                "client_secret": self.secret,
                "grant_type": "client_credentials",
            }:
                return httpx.Response(401, json={"ErrorMessage": "Invalid client"})
            self.tokens.append(f"token-{len(self.tokens) + 1}")
            return httpx.Response(
                200,
                json={"access_token": self.tokens[-1], "expires_in": 599, "token_type": "Bearer"},
            )

        assert (request.method, request.url.path) == ("POST", "/products/v4/search/keyword")
        if request.headers["Authorization"] != f"Bearer {self.tokens[-1] if self.tokens else ''}":
            return httpx.Response(401, json={"title": "Unauthorized"})
        self.headers.append(request.headers)
        self.searches.append(json.loads(request.content)["Keywords"])
        if self.answer is not None:
            return httpx.Response(self.answer[0], text=self.answer[1])
        return httpx.Response(
            200,
            headers={"X-RateLimit-Remaining": str(1000 - len(self.searches))},
            json={
                "Products": self.products,
                "ProductsCount": len(self.products),
                "ExactMatches": [],
                "SearchLocaleUsed": {"Site": "DE", "Language": "en", "Currency": "EUR"},
            },
        )


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def pass_(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def server():
    return FakeDigiKey()


@pytest.fixture()
def digikey(server, clock):
    return DigiKey(
        "the-client",
        "s3cret",
        site="DE",
        language="en",
        currency="EUR",
        transport=httpx.MockTransport(server),
        clock=clock,
    )


YAGEO = Query("RC0603FR-0710KL", "Yageo")


# -- DigiKey: asking ------------------------------------------------------------


def test_a_search_is_sent_as_digikey_wants_it(digikey, server):
    fetched = digikey.fetch(YAGEO)

    assert (fetched.status, fetched.answered, fetched.remaining) == (200, True, 999)
    assert server.searches == ["RC0603FR-0710KL"]
    sent = server.headers[0]
    assert sent["X-DIGIKEY-Client-Id"] == "the-client"
    assert sent["Authorization"] == "Bearer token-1"
    assert (
        sent["X-DIGIKEY-Locale-Site"],
        sent["X-DIGIKEY-Locale-Language"],
        sent["X-DIGIKEY-Locale-Currency"],
    ) == ("DE", "en", "EUR")


def test_a_token_is_kept_for_as_long_as_it_lasts(digikey, server, clock):
    digikey.fetch(YAGEO)
    clock.pass_(500)
    digikey.fetch(YAGEO)
    assert server.tokens == ["token-1"], "ten minutes: asked for once"

    clock.pass_(100)  # 600 seconds on: it has run out
    digikey.fetch(YAGEO)
    assert server.tokens == ["token-1", "token-2"]
    assert len(server.searches) == 3


def test_a_token_withdrawn_early_is_replaced_and_the_search_made_again(digikey, server):
    digikey.fetch(YAGEO)
    server.tokens.append("token-withdrawn-and-reissued")  # the one we hold is no longer the last

    fetched = digikey.fetch(YAGEO)

    assert fetched.status == 200
    assert len(server.tokens) == 3, "refused once, and a new one asked for"


def test_keys_that_are_wrong_are_said_without_saying_the_secret(server):
    wrong = DigiKey("the-client", "not-the-secret", transport=httpx.MockTransport(server))

    with pytest.raises(SupplierError) as refused:
        wrong.fetch(YAGEO)

    assert "HTTP 401" in str(refused.value) and "DIGIKEY_CLIENT_SECRET" in str(refused.value)
    assert "not-the-secret" not in str(refused.value)
    assert server.searches == []


def test_a_supplier_that_cannot_be_reached_is_an_error_not_an_answer(digikey, server):
    server.down = True

    with pytest.raises(SupplierError, match="could not be reached"):
        digikey.fetch(YAGEO)


def test_without_keys_digikey_is_not_set_up(server):
    for keys in ((None, None), ("the-client", None), (None, "s3cret"), ("", "")):
        nobody = DigiKey(*keys, transport=httpx.MockTransport(server))
        assert nobody.configured is False
        with pytest.raises(SupplierError, match="not set up"):
            nobody.fetch(YAGEO)
    assert server.tokens == [] and server.searches == []


def test_what_is_asked_is_the_number_and_whose_prices(digikey):
    """Two queries that send the same thing are one question."""
    assert digikey.asks(Query(" rc0603fr-0710kl ", "Yageo")) == digikey.asks(
        Query("RC0603FR-0710KL", "Somebody Else")
    )
    assert digikey.asks(YAGEO) != digikey.asks(Query("RC0603FR-0710KJ", "Yageo"))
    dollars = DigiKey("the-client", "s3cret", site="US", currency="USD")
    assert digikey.asks(YAGEO) != dollars.asks(YAGEO), "another country, another price"


# -- DigiKey: reading -----------------------------------------------------------


def body(*products, **more):
    return json.dumps({"Products": list(products), "ExactMatches": [], **more})


def test_an_answer_is_read_as_an_offer(digikey):
    answer = body(product(), SearchLocaleUsed={"Site": "DE", "Language": "en", "Currency": "EUR"})

    offer = digikey.read(answer, YAGEO, NOBODY)

    assert offer == Offer(
        supplier="digikey",
        mpn="RC0603FR-0710KL",
        manufacturer="YAGEO",
        sku="311-10.0KHRCT-ND",
        description="RES 10K OHM 1% 1/10W 0603",
        stock=7557063,
        lifecycle="active",
        status="Active",
        # The packaging with the smallest order: what a prototype is bought in.
        prices=(
            PriceBreak(1, 0.1),
            PriceBreak(10, 0.021),
            PriceBreak(100, 0.0107),
            PriceBreak(1000, 0.0061),
        ),
        currency="EUR",
        packaging="Cut Tape (CT)",
        minimum=1,
        lead_weeks=18,
        url="https://www.digikey.de/en/products/detail/yageo/RC0603FR-0710KL/726880",
        datasheet="https://www.yageo.com/upload/media/datasheet.pdf",
    )
    assert (offer.price_at(1), offer.price_at(99), offer.price_at(5000)) == (0.1, 0.021, 0.0061)


def test_only_the_number_asked_for_is_the_part_meant(digikey):
    """A search also returns what is merely like it."""
    alike = body(product("RC0603FR-0710KP"), product("RC0603FR-0710K"))

    assert digikey.read(alike, YAGEO, NOBODY) is None
    assert digikey.read(body(product("rc0603fr-0710kl ")), YAGEO, NOBODY).mpn == "rc0603fr-0710kl "


def test_one_number_from_two_makers_is_told_apart_by_the_maker(digikey):
    known = Manufacturers((Manufacturer("Texas Instruments", ("TI",)),))
    both = body(product("LM358", "onsemi"), product("LM358", "Texas Instruments"))

    assert digikey.read(both, Query("LM358", "TI"), known).manufacturer == "Texas Instruments"
    assert digikey.read(both, Query("LM358", "ON Semi"), known) is None, "neither is known to be it"
    assert digikey.read(both, Query("LM358", "TI"), NOBODY) is None, "nor without the list"
    assert digikey.read(both, Query("LM358", ""), NOBODY) is None


def test_the_only_part_by_that_number_is_taken_whatever_its_maker_is_called(digikey):
    """A number is very nearly a name. The offer says who DigiKey says makes it."""
    only = body(product("LM358", "Texas Instruments"))

    assert digikey.read(only, Query("LM358", "TI"), NOBODY).manufacturer == "Texas Instruments"


@pytest.mark.parametrize(
    "status,flags,expected",
    [
        ("Active", {}, "active"),
        ("Obsolete", {}, "obsolete"),
        ("Not For New Designs", {}, "nrnd"),
        ("Last Time Buy", {}, "nrnd"),
        ("Active", {"end_of_life": True}, "nrnd"),
        # The distributor's decision, which says nothing about the manufacturer.
        ("Discontinued at DigiKey", {"discontinued": True}, "unknown"),
        ("Active", {"discontinued": True}, "unknown"),
        ("Preliminary", {}, "unknown"),
        ("", {}, "unknown"),
    ],
)
def test_digikey_s_status_in_the_library_s_words(status, flags, expected):
    assert lifecycle_of(status, **flags) == expected


def test_an_answer_that_lacks_things_is_still_read(digikey):
    bare = body({"ManufacturerProductNumber": "RC0603FR-0710KL", "ProductUrl": "javascript:x"})

    offer = digikey.read(bare, YAGEO, NOBODY)

    assert (offer.stock, offer.prices, offer.status, offer.lifecycle) == (None, (), "", "unknown")
    assert offer.url is None, "only a web address is a link"
    assert offer.currency == "EUR", "the one that was asked for"


@pytest.mark.parametrize("answer", ["", "not json", "[]", '{"Products": "no"}', '{"Products": []}'])
def test_an_answer_that_is_not_one_says_nothing(digikey, answer):
    assert digikey.read(answer, YAGEO, NOBODY) is None


def test_a_marketplace_seller_s_packaging_is_not_the_one_shown(digikey):
    theirs = variation("2-SELLER", "Bulk", 1, [(1, 0.01)], MarketPlace=True)
    ours = variation("311-CT", "Cut Tape (CT)", 10, [(10, 0.02)])

    offer = digikey.read(body(product(ProductVariations=[theirs, ours])), YAGEO, NOBODY)

    assert (offer.sku, offer.minimum, offer.price_at(1)) == ("311-CT", 10, None)


# -- what is kept -----------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    return SupplierStore(Database(tmp_path / "app.db"))


def test_an_answer_is_kept_under_who_was_asked_what(store):
    assert store.get("digikey", "keyword X") is None

    store.put("digikey", "keyword X", 200, "{}", now=1000.0, holds_for=60.0)
    kept = store.get("digikey", "keyword X")

    assert (kept.status, kept.body, kept.fetched_at, kept.expires_at) == (200, "{}", 1000.0, 1060.0)
    assert kept.holds(1059.0) and not kept.holds(1060.0)
    assert store.get("mouser", "keyword X") is None, "another supplier's answer is another"

    store.put("digikey", "keyword X", 404, "", now=2000.0, holds_for=60.0)
    assert store.get("digikey", "keyword X").status == 404, "the newer in place of the older"


def test_an_answer_long_out_of_date_is_thrown_away(store):
    store.put("digikey", "old", 200, "{}", now=0.0, holds_for=60.0)
    store.put("digikey", "new", 200, "{}", now=KEEP_STALE_SECONDS + 30.0, holds_for=60.0)
    assert store.get("digikey", "old") is not None, "stale, but still to fall back on"

    store.put("digikey", "newer", 200, "{}", now=KEEP_STALE_SECONDS + 90.0, holds_for=60.0)
    assert store.get("digikey", "old") is None


def test_calls_are_counted_by_supplier_and_day(store):
    midnight = 1_800_000_000.0 - 1_800_000_000.0 % DAY

    assert store.calls("digikey", midnight) == 0
    assert [store.count_call("digikey", midnight + n) for n in range(3)] == [1, 2, 3]
    assert store.count_call("mouser", midnight) == 1
    assert store.calls("digikey", midnight + DAY - 1) == 3, "the same day, in UTC"
    assert store.calls("digikey", midnight + DAY) == 0, "and a new one"

    store.put("digikey", "q", 200, "{}", now=midnight + 5, holds_for=60.0)
    used = store.usage("digikey", midnight + 10)
    assert (used.calls_today, used.answers, used.last_asked) == (3, 1, midnight + 5)


# -- when a supplier is asked at all ----------------------------------------------


@pytest.fixture()
def supply(digikey, store, clock):
    return Supply([digikey], store, limits={"digikey": 3}, holds_for_seconds=DAY, clock=clock)


def test_the_same_thing_is_not_asked_twice_while_the_answer_holds(supply, server, clock):
    (first,) = supply.ask(YAGEO, NOBODY)
    clock.pass_(DAY - 1)
    (again,) = supply.ask(YAGEO, NOBODY)
    # The same number under another spelling of its maker sends the same thing.
    (spelt,) = supply.ask(Query("rc0603fr-0710kl", "YAGEO Corp."), NOBODY)

    assert server.searches == ["RC0603FR-0710KL"], "one call, however often it is looked at"
    assert (first.state, first.offer.stock, first.stale) == (State.FOUND, 7557063, False)
    assert again == first and spelt.offer == first.offer
    assert first.asked_at == clock.now - (DAY - 1)

    clock.pass_(1)  # the answer no longer holds
    supply.ask(YAGEO, NOBODY)
    assert len(server.searches) == 2


def test_a_part_the_supplier_does_not_list_is_an_answer_too(supply, server):
    server.products = []

    (answer,) = supply.ask(Query("NO-SUCH-PART", "Nobody"), NOBODY)
    supply.ask(Query("NO-SUCH-PART", "Nobody"), NOBODY)

    assert (answer.state, answer.offer) == (State.NOT_FOUND, None)
    assert len(server.searches) == 1, "and is not asked for again either"


def test_when_today_s_calls_are_used_up_nothing_more_is_asked(supply, server, store, clock):
    for number in ("A", "B", "C"):
        supply.ask(Query(number), NOBODY)
    assert store.calls("digikey", clock.now) == 3

    (refused,) = supply.ask(Query("D"), NOBODY)

    assert (refused.state, refused.offer, refused.asked_at) == (State.OVER_LIMIT, None, None)
    assert server.searches == ["A", "B", "C"]

    clock.pass_(DAY)  # a new day, and a new allowance
    supply.ask(Query("D"), NOBODY)
    assert server.searches == ["A", "B", "C", "D"]


def test_an_old_answer_is_better_than_none_and_is_said_to_be_old(supply, server, clock):
    (fresh,) = supply.ask(YAGEO, NOBODY)
    clock.pass_(DAY + 60)  # it no longer holds
    for number in ("A", "B", "C"):  # and today's calls go on other things
        supply.ask(Query(number), NOBODY)

    (old,) = supply.ask(YAGEO, NOBODY)

    assert (old.state, old.stale, old.offer) == (State.FOUND, True, fresh.offer)
    assert old.asked_at == fresh.asked_at, "when it was asked, not when it was shown"
    assert server.searches.count("RC0603FR-0710KL") == 1


def test_a_supplier_that_fails_is_left_alone_for_a_while(supply, server, store, clock):
    server.answer = (503, "upstream connect error")

    (failed,) = supply.ask(YAGEO, NOBODY)
    supply.ask(YAGEO, NOBODY)

    assert failed.state is State.FAILED
    assert len(server.searches) == 1, "not asked again at once"
    assert store.calls("digikey", clock.now) == 1, "though the one call was a call"
    assert store.get("digikey", supply._suppliers[0].asks(YAGEO)) is None, "a failure is not kept"

    server.answer = None
    clock.pass_(RETRY_AFTER_SECONDS + 1)
    (answered,) = supply.ask(YAGEO, NOBODY)
    assert answered.state is State.FOUND


def test_a_supplier_that_cannot_be_reached_falls_back_on_what_is_kept(supply, server, clock):
    (fresh,) = supply.ask(YAGEO, NOBODY)
    clock.pass_(DAY + 1)
    server.down = True

    (old,) = supply.ask(YAGEO, NOBODY)
    (unknown,) = supply.ask(Query("NEVER-ASKED"), NOBODY)

    assert (old.state, old.stale, old.offer) == (State.FOUND, True, fresh.offer)
    assert (unknown.state, unknown.offer) == (State.FAILED, None)


def test_a_supplier_that_says_too_many_is_not_asked_anything_more(supply, server, clock):
    server.answer = (429, '{"title": "Daily Ratelimit exceeded"}')

    (refused,) = supply.ask(YAGEO, NOBODY)
    (other,) = supply.ask(Query("SOMETHING-ELSE"), NOBODY)

    assert (refused.state, other.state) == (State.OVER_LIMIT, State.FAILED)
    assert len(server.searches) == 1, "its count is ahead of ours: nothing more goes to it"


def test_a_supplier_without_keys_is_listed_and_not_asked(store, server, clock):
    nobody = DigiKey(None, None, transport=httpx.MockTransport(server))
    supply = Supply([nobody], store, limits={"digikey": 1000}, holds_for_seconds=DAY, clock=clock)

    assert supply.enabled is False
    assert supply.ask(YAGEO, NOBODY) == ()
    (listed,) = supply.usage()
    assert (listed.name, listed.configured, listed.calls_today, listed.limit) == (
        "DigiKey",
        False,
        0,
        1000,
    )


def test_usage_says_how_much_of_the_day_is_gone(supply):
    supply.ask(YAGEO, NOBODY)
    supply.ask(Query("B"), NOBODY)

    (used,) = supply.usage()

    assert (used.configured, used.calls_today, used.limit, used.answers) == (True, 2, 3, 2)
    assert used.last_asked is not None


def test_with_nowhere_to_keep_answers_nothing_is_asked(digikey, server, tmp_path, clock):
    """Without the answers and the count, every look at a page would be a call."""
    nowhere = SupplierStore(Database(tmp_path / "missing" / "app.db"))
    supply = Supply([digikey], nowhere, limits={"digikey": 3}, holds_for_seconds=DAY, clock=clock)

    with pytest.raises(DatabaseUnavailable):
        supply.ask(YAGEO, NOBODY)

    assert server.searches == []
    assert supply.usage()[0].calls_today == 0, "and the status page still draws"


@pytest.mark.parametrize(
    "said,recorded,expected",
    [
        ("obsolete", "active", True),
        ("nrnd", "active", True),
        ("obsolete", "nrnd", True),
        ("nrnd", "unknown", True),
        ("obsolete", None, True),
        ("active", "active", False),
        ("active", "obsolete", False),
        ("nrnd", "obsolete", False),
        ("unknown", "active", False),
        ("active", None, False),
    ],
)
def test_a_supplier_that_says_a_part_stands_worse_is_the_reason_to_look(said, recorded, expected):
    assert stands_worse(said, recorded) is expected


# -- through the web --------------------------------------------------------------


@pytest.fixture()
def site(library_client, digikey, clock):
    """A library with one resistor, and DigiKey to ask about it."""
    library_client.app.state.supply = Supply(
        [digikey],
        SupplierStore(library_client.app.state.database),
        limits={"digikey": 1000},
        holds_for_seconds=DAY,
        clock=clock,
    )
    return library_client


def test_a_part_s_manufacturer_parts_come_with_what_suppliers_say(site, server):
    supply = site.get("/api/v1/parts/1102-0001/supply").json()

    (source,) = supply["sources"]
    (answer,) = source["answers"]
    assert (supply["ipn"], source["mpn"], source["manufacturer"], source["lifecycle"]) == (
        "1102-0001",
        "RC0603FR-0710KL",
        "Yageo",
        "active",
    )
    assert (answer["supplier"], answer["name"], answer["state"], answer["stale"]) == (
        "digikey",
        "DigiKey",
        "found",
        False,
    )
    assert (answer["stock"], answer["status"], answer["warns"], answer["currency"]) == (
        7557063,
        "Active",
        False,
        "EUR",
    )
    assert answer["prices"][0] == {"quantity": 1, "unit_price": 0.1}
    assert answer["url"].startswith("https://www.digikey.de/")

    site.get("/api/v1/parts/1102-0001/supply")
    assert len(server.searches) == 1, "looked at twice, asked once"


def test_a_part_the_supplier_calls_obsolete_is_marked(site, server):
    server.products = [product(status="Obsolete")]

    (answer,) = site.get("/api/v1/parts/1102-0001/supply").json()["sources"][0]["answers"]

    assert (answer["lifecycle"], answer["status"], answer["warns"]) == (
        "obsolete",
        "Obsolete",
        True,
    ), "the library has it as active"


def test_the_part_page_leaves_room_for_it_only_where_there_is_a_supplier(site, library_client):
    page = site.get("/ipn/1102-0001").text
    assert '<div id="supply" data-ipn="1102-0001"' in page
    assert 'type="module" src="/static/js/supply.js"' in page

    library_client.app.state.supply = Supply(
        [DigiKey(None, None)],
        SupplierStore(library_client.app.state.database),
        limits={},
        holds_for_seconds=DAY,
    )
    bare = library_client.get("/ipn/1102-0001").text
    assert 'id="supply"' not in bare and "supply.js" not in bare
    assert (
        library_client.get("/api/v1/parts/1102-0001/supply").json()["sources"][0]["answers"] == []
    )


def test_the_status_page_says_how_much_each_supplier_has_been_asked(site, client):
    site.get("/api/v1/parts/1102-0001/supply")
    page = site.get("/").text
    assert "<strong>DigiKey</strong>" in page and "1 of 1000" in page

    bare = client.get("/").text
    assert "not set up" in bare and "DIGIKEY_CLIENT_ID" in bare


def test_asking_suppliers_needs_a_session(client):
    assert client.get("/api/v1/parts/1102-0001/supply").status_code == 401


def test_the_supply_script_is_a_module_that_marks_its_links_for_kicad(library_client):
    script = library_client.get("/static/js/supply.js").text

    assert 'import { api, el } from "./app.js";' in script
    assert "link.dataset.external" in script, "inside the panel, the address is offered to copy"


# -- settings and copies ----------------------------------------------------------


def test_digikey_is_set_up_from_the_environment():
    settings = Settings.from_env(
        {
            **ENVIRONMENT,
            "DIGIKEY_CLIENT_ID": "the-client",
            "DIGIKEY_CLIENT_SECRET": "s3cret",
            "DIGIKEY_API_URL": "https://sandbox-api.digikey.com/",
            "DIGIKEY_LOCALE_SITE": "de",
            "DIGIKEY_LOCALE_CURRENCY": "eur",
            "DIGIKEY_DAILY_LIMIT": "250",
            "SUPPLIER_CACHE_HOURS": "0",
        }
    )

    assert (settings.digikey_api_url, settings.digikey_site, settings.digikey_currency) == (
        "https://sandbox-api.digikey.com",
        "DE",
        "EUR",
    )
    assert (settings.digikey_daily_limit, settings.supplier_cache_hours) == (250, 1)
    assert not [problem for problem in settings.problems() if "DIGIKEY" in problem]


@pytest.mark.parametrize(
    "environment,said",
    [
        ({"DIGIKEY_CLIENT_ID": "the-client"}, "DIGIKEY_CLIENT_SECRET is not set"),
        ({"DIGIKEY_CLIENT_SECRET": "s3cret"}, "DIGIKEY_CLIENT_ID is not set"),
        ({"DIGIKEY_API_URL": "http://api.digikey.com"}, "must be an https address"),
    ],
)
def test_half_a_set_of_keys_is_a_problem_to_report(environment, said):
    problems = Settings.from_env({**ENVIRONMENT, **environment}).problems()

    assert any(said in problem for problem in problems)
    assert not any("s3cret" in problem for problem in problems)


def test_a_copy_of_the_database_leaves_out_what_suppliers_answered(store, tmp_path):
    """It can be had again by asking, and would otherwise be most of the file."""
    store.put("digikey", "q", 200, "{}", now=1000.0, holds_for=60.0)
    store.count_call("digikey", 1000.0)

    copy_database(store.database.path, tmp_path / "copy.db")

    with sqlite3.connect(tmp_path / "copy.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM supplier_answers").fetchone()[0] == 0
        assert connection.execute("SELECT calls FROM supplier_calls").fetchone()[0] == 1
    assert store.get("digikey", "q") is not None, "the database itself is not touched"
