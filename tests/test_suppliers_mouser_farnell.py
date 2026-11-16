"""Mouser and Farnell, and what asking more than one supplier takes.

Each is stood in for by an httpx transport that answers the way its API does,
as its public documentation shows and as it answered a made-up key when asked:
Mouser with HTTP 200 and the reason in `Errors`, Farnell's gateway with HTTP
403 and the reason in a header.
"""

import json
import logging
import urllib.parse

import httpx
import pytest

from app.application import create_app
from app.config import Settings
from app.db.database import Database
from app.domain.manufacturers import Manufacturer, Manufacturers
from app.domain.supply import PriceBreak, Query
from app.observability import configure_logging
from app.services.supply import PATIENCE_SECONDS, RETRY_AFTER_SECONDS, Rate, State, Supply
from app.suppliers import farnell as farnell_module
from app.suppliers import mouser as mouser_module
from app.suppliers.base import SupplierError, TooManyCalls
from app.suppliers.farnell import Farnell, currency_of
from app.suppliers.mouser import Mouser
from app.suppliers.reading import amount, matching
from app.suppliers.store import SupplierStore
from tests.conftest import ENVIRONMENT
from tests.test_suppliers import Clock, FakeDigiKey

NOBODY = Manufacturers()
DAY = 24 * 3600
YAGEO = Query("RC0603FR-0710KL", "Yageo")
KEY = "the-secret-key-0000"


@pytest.fixture()
def clock():
    return Clock()


# -- reading any supplier's answer ----------------------------------------------


@pytest.mark.parametrize(
    "written,comma_is_decimal,price",
    [
        ("$0.10", False, 0.10),
        ("$1,234.56", False, 1234.56),
        ("0,089 €", True, 0.089),
        ("1.234,56 €", True, 1234.56),
        ("1,234 €", True, 1.234),
        ("$1,234", False, 1234.0),
        ("€0.089", True, 0.089),  # the euro, where numbers are written the English way
        ("CHF 1'234.50", False, 1234.50),
        ("12 345,67 kr", True, 12345.67),
    ],
)
def test_a_price_written_for_people_is_a_number(written, comma_is_decimal, price):
    assert amount(written, comma_is_decimal=comma_is_decimal) == pytest.approx(price)


def test_text_with_no_price_in_it_is_none():
    assert amount("", comma_is_decimal=True) is None
    assert amount("call for price", comma_is_decimal=False) is None


def chosen(products, query, manufacturers=NOBODY):
    found = matching(
        products, query, manufacturers, number_of=lambda p: p[0], maker_of=lambda p: p[1]
    )
    return [product[2] for product in found]


def test_what_goes_by_the_number_from_the_maker_asked_for_is_what_is_meant():
    products = [
        ("LM358", "Texas Instruments", "ti-tube"),
        ("LM358", "onsemi", "on"),
        ("LM358", "TI", "ti-reel"),
        ("LM358DR", "Texas Instruments", "another part"),
    ]
    listed = Manufacturers((Manufacturer("Texas Instruments", ("TI",)),))

    assert chosen(products, Query("lm358", "TI"), listed) == ["ti-tube", "ti-reel"], "every form"
    assert chosen(products, Query("LM358", "Texas Instruments")) == [
        "ti-tube"
    ], "without the list, TI is not known to be the same"
    assert chosen(products, Query("LM358", "Somebody Else")) == [], "two makers: cannot be told"
    assert chosen(products, Query("LM358")) == []
    assert chosen(products, Query("LM35")) == [], "what is merely like it is not it"


def test_everything_by_a_number_from_one_maker_is_taken_whatever_it_is_called():
    products = [("X1", "Acme Corp.", "reel"), ("X1", "ACME", "tape")]

    assert chosen(products, Query("X1", "Somebody Else")) == ["reel", "tape"]
    assert chosen(products, Query("X1")) == ["reel", "tape"]


# -- Mouser ---------------------------------------------------------------------


def mouser_part(mpn="RC0603FR-0710KL", maker="YAGEO", **more):
    """A part as Mouser's part number search returns one."""
    return {
        "Availability": "6609 In Stock",
        "AvailabilityInStock": "6609",
        "DataSheetUrl": "https://www.mouser.com/datasheet/2/447/PYu_RC_Group_51_RoHS_L_12.pdf",
        "Description": "Thick Film Resistors - SMD 10 kOhms 100 mW 0603 1%",
        "LeadTime": "56 Days",
        "LifecycleStatus": None,
        "Manufacturer": maker,
        "ManufacturerPartNumber": mpn,
        "Min": "1",
        "Mult": "1",
        "MouserPartNumber": f"603-{mpn}",
        "ProductAttributes": [
            {"AttributeName": "Packaging", "AttributeValue": "Reel"},
            {"AttributeName": "Packaging", "AttributeValue": "Cut Tape"},
            {"AttributeName": "Standard Pack Qty", "AttributeValue": "5000"},
        ],
        "PriceBreaks": [
            {"Quantity": 1, "Price": "0,089 €", "Currency": "EUR"},
            {"Quantity": 10, "Price": "0,02 €", "Currency": "EUR"},
            {"Quantity": 100, "Price": "0,009 €", "Currency": "EUR"},
        ],
        "ProductDetailUrl": f"https://www.mouser.de/ProductDetail/YAGEO/{mpn}?qs=abc",
        "IsDiscontinued": "false",
        **more,
    }


class FakeMouser:
    """Mouser's Search API, as far as this app uses it."""

    def __init__(self):
        self.parts = [mouser_part()]
        self.requests = []
        self.errors = None  # to refuse with, the way Mouser does: HTTP 200
        self.answer = None  # (status, body) to give instead
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError(f"no route to {request.url}", request=request)
        assert (request.method, request.url.path) == ("POST", "/api/v1/search/partnumber")
        self.requests.append(request)
        if request.url.params["apiKey"] != KEY:
            return httpx.Response(
                200,
                json={
                    "Errors": [
                        {
                            "Id": 0,
                            "Code": "Invalid",
                            "Message": "Invalid unique identifier.",
                            "ResourceKey": "InvalidIdentifier",
                            "PropertyName": "API Key",
                        }
                    ],
                    "SearchResults": None,
                },
            )
        if self.errors is not None:
            return httpx.Response(200, json={"Errors": self.errors, "SearchResults": None})
        if self.answer is not None:
            return httpx.Response(self.answer[0], text=self.answer[1])
        return httpx.Response(
            200,
            json={
                "Errors": [],
                "SearchResults": {"NumberOfResult": len(self.parts), "Parts": self.parts},
            },
        )

    @property
    def searches(self):
        return [
            json.loads(request.content)["SearchByPartRequest"]["mouserPartNumber"]
            for request in self.requests
        ]


@pytest.fixture()
def mouser_server():
    return FakeMouser()


@pytest.fixture()
def mouser(mouser_server):
    return Mouser(KEY, transport=httpx.MockTransport(mouser_server))


def test_a_search_is_sent_as_mouser_wants_it(mouser, mouser_server):
    fetched = mouser.fetch(YAGEO)

    assert (fetched.status, fetched.answered) == (200, True)
    (request,) = mouser_server.requests
    assert str(request.url).startswith("https://api.mouser.com/api/v1/search/partnumber?apiKey=")
    assert json.loads(request.content) == {
        "SearchByPartRequest": {
            "mouserPartNumber": "RC0603FR-0710KL",
            "partSearchOptions": "None",
        }
    }
    assert mouser.asks(Query(" rc0603fr-0710kl ")) == mouser.asks(YAGEO)


def test_mouser_s_answer_is_read_as_an_offer(mouser, mouser_server):
    offer = mouser.read(mouser.fetch(YAGEO).body, YAGEO, NOBODY)

    assert (offer.supplier, offer.mpn, offer.manufacturer, offer.sku) == (
        "mouser",
        "RC0603FR-0710KL",
        "YAGEO",
        "603-RC0603FR-0710KL",
    )
    assert (offer.stock, offer.lifecycle, offer.status) == (6609, "active", "")
    assert offer.prices == (PriceBreak(1, 0.089), PriceBreak(10, 0.02), PriceBreak(100, 0.009))
    assert (offer.currency, offer.minimum, offer.lead_weeks) == ("EUR", 1, 8)
    assert offer.packaging == "Reel, Cut Tape"
    assert offer.url.startswith("https://www.mouser.de/ProductDetail/")
    assert offer.datasheet.endswith(".pdf")


def test_the_price_as_a_number_is_taken_where_mouser_gives_one(mouser):
    part = mouser_part(
        PriceBreaks=[
            {"Quantity": 1, "Price": "$1,234.50", "RawPrice": 1234.5, "Currency": "USD"},
            {"Quantity": 10, "Price": "$1,100.00", "Currency": "USD"},
        ]
    )
    body = json.dumps({"Errors": [], "SearchResults": {"Parts": [part]}})

    offer = mouser.read(body, YAGEO, NOBODY)

    assert offer.prices == (PriceBreak(1, 1234.5), PriceBreak(10, 1100.0))
    assert offer.currency == "USD"


def test_only_the_part_that_goes_by_exactly_the_number_is_mouser_s_answer(mouser):
    """The search returns everything that begins with the number."""
    parts = [
        mouser_part("RC0603FR-0710KLTR", Min="5000"),
        mouser_part("RC0603FR-0710KL", Min="5000", MouserPartNumber="reel"),
        mouser_part("RC0603FR-0710KL", Min="1", MouserPartNumber="tape"),
        mouser_part("RC0603FR-0710KL", "Somebody Else", MouserPartNumber="theirs"),
    ]
    body = json.dumps({"Errors": [], "SearchResults": {"Parts": parts}})

    assert mouser.read(body, YAGEO, NOBODY).sku == "tape", "the form with the smallest order"
    assert mouser.read(body, Query("RC0603FR-07"), NOBODY) is None
    assert mouser.read(body, Query("RC0603FR-0710KL", "Vishay"), NOBODY) is None, "two makers"


@pytest.mark.parametrize(
    "status,discontinued,lifecycle",
    [
        (None, False, "active"),
        ("New Product", False, "active"),
        ("Not Recommended for New Designs", False, "nrnd"),
        ("End of Life", False, "nrnd"),
        ("Obsolete", False, "obsolete"),
        ("", True, "unknown"),  # Mouser no longer sells it; the maker may still make it
        ("Factory Special Order", False, "unknown"),
    ],
)
def test_mouser_s_status_in_the_library_s_words(status, discontinued, lifecycle):
    assert mouser_module.lifecycle_of(status or "", discontinued=discontinued) == lifecycle


@pytest.mark.parametrize(
    "fields,stock",
    [
        ({"AvailabilityInStock": "6609"}, 6609),
        ({"AvailabilityInStock": None, "Availability": "12 In Stock"}, 12),
        ({"AvailabilityInStock": None, "Availability": "None"}, 0),
        ({"AvailabilityInStock": None, "Availability": "On Order"}, None),
    ],
)
def test_mouser_s_stock_is_read_from_the_number_or_the_sentence(mouser, fields, stock):
    body = json.dumps({"SearchResults": {"Parts": [mouser_part(**fields)]}})

    assert mouser.read(body, YAGEO, NOBODY).stock == stock


@pytest.mark.parametrize("answer", ["", "not json", "[]", '{"SearchResults": null}', "{}"])
def test_an_answer_from_mouser_that_is_not_one_says_nothing(mouser, answer):
    assert mouser.read(answer, YAGEO, NOBODY) is None


def test_a_key_mouser_does_not_know_is_a_refusal_not_an_answer(mouser_server):
    """Mouser answers HTTP 200 to a key it does not know. Taken for an answer
    that would be kept for a day as "Mouser does not list it"."""
    wrong = Mouser("not-the-key-1234", transport=httpx.MockTransport(mouser_server))

    with pytest.raises(SupplierError) as refused:
        wrong.fetch(YAGEO)

    said = str(refused.value)
    assert "Invalid unique identifier. (API Key)" in said and "MOUSER_API_KEY" in said
    assert "not-the-key-1234" not in said
    assert not isinstance(refused.value, TooManyCalls)


def test_mouser_saying_too_many_calls_is_said_as_that(mouser, mouser_server):
    mouser_server.errors = [
        {"Code": "TooManyRequests", "Message": "Maximum calls per day exceeded."}
    ]
    with pytest.raises(TooManyCalls, match="Maximum calls per day exceeded"):
        mouser.fetch(YAGEO)

    mouser_server.errors = None
    mouser_server.answer = (429, "slow down")
    with pytest.raises(TooManyCalls, match="HTTP 429"):
        mouser.fetch(YAGEO)


def test_nothing_mouser_says_or_fails_with_gives_the_key_away(mouser, mouser_server):
    mouser_server.down = True
    with pytest.raises(SupplierError) as unreachable:
        mouser.fetch(YAGEO)
    assert "could not be reached" in str(unreachable.value)
    assert KEY not in str(unreachable.value), "the address it could not reach has the key in it"

    mouser_server.down = False
    mouser_server.answer = (500, f"error handling /search?apiKey={KEY}")
    fetched = mouser.fetch(YAGEO)
    assert (fetched.status, fetched.answered) == (500, False)
    assert KEY not in fetched.said and "apiKey=…" in fetched.said


def test_without_a_key_mouser_is_not_set_up(mouser_server):
    nobody = Mouser(None, transport=httpx.MockTransport(mouser_server))

    assert (nobody.configured, nobody.keys) == (False, ("MOUSER_API_KEY",))
    with pytest.raises(SupplierError, match="not set up"):
        nobody.fetch(YAGEO)
    assert mouser_server.requests == []


# -- Farnell --------------------------------------------------------------------


def farnell_product(mpn="RC0603FR-0710KL", maker="YAGEO", **more):
    """A product as the Product Search API returns one, in the large group."""
    return {
        "sku": "9238603",
        "displayName": f"{maker} - {mpn} - SMD Chip Resistor, 10 kohm, ± 1%, 100 mW, 0603",
        "productStatus": "STOCKED",
        "rohsStatusCode": "YES",
        "packSize": 1,
        "unitOfMeasure": "EACH",
        "datasheets": [
            {
                "type": "T",
                "description": "Technical Data Sheet",
                "url": "https://www.farnell.com/datasheets/3437310.pdf",
            }
        ],
        "prices": [
            {"to": 99, "from": 10, "cost": 0.0142},
            {"to": 499, "from": 100, "cost": 0.0079},
            {"to": 1000000000, "from": 500, "cost": 0.0052},
        ],
        "inv": 518,
        "vendorName": maker,
        "brandName": maker,
        "translatedManufacturerPartNumber": mpn,
        "translatedMinimumOrderQuality": 10,
        "stock": {
            "level": 518,
            "leastLeadTime": 63,
            "status": 1,
            "shipsFromMultipleWarehouses": True,
        },
        "releaseStatusCode": 4,
        **more,
    }


class FakeFarnell:
    """The Product Search API, behind the gateway that guards it."""

    def __init__(self):
        self.products = [farnell_product()]
        self.requests = []
        self.gateway = None  # an error code to turn the call away with
        self.answer = None  # (status, body) to give instead
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError(f"no route to {request.url}", request=request)
        assert (request.method, request.url.path) == ("GET", "/catalog/products")
        self.requests.append(request)
        if request.url.params["callInfo.apiKey"] != KEY:
            return httpx.Response(
                403,
                headers={
                    "X-Mashery-Error-Code": "ERR_403_DEVELOPER_INACTIVE",
                    "X-Error-Detail-Header": "Account Inactive",
                },
                text="<h1>Developer Inactive</h1>",
            )
        if self.gateway is not None:
            return httpx.Response(
                403,
                headers={"X-Mashery-Error-Code": self.gateway},
                text="<h1>Developer Over Qps</h1>",
            )
        if self.answer is not None:
            return httpx.Response(self.answer[0], text=self.answer[1])
        found = {"numberOfResults": len(self.products)}
        if self.products:
            found["products"] = self.products
        return httpx.Response(200, json={"manufacturerPartNumberSearchReturn": found})


@pytest.fixture()
def farnell_server():
    return FakeFarnell()


@pytest.fixture()
def farnell(farnell_server):
    return Farnell(KEY, store="de.farnell.com", transport=httpx.MockTransport(farnell_server))


def test_a_search_is_sent_as_farnell_wants_it(farnell, farnell_server):
    fetched = farnell.fetch(YAGEO)

    assert (fetched.status, fetched.answered) == (200, True)
    (request,) = farnell_server.requests
    assert request.url.host == "api.element14.com"
    sent = dict(urllib.parse.parse_qsl(request.url.query.decode()))
    assert sent == {
        "term": "manuPartNum:RC0603FR-0710KL",
        "storeInfo.id": "de.farnell.com",
        "resultsSettings.offset": "0",
        "resultsSettings.numberOfResults": "10",
        "resultsSettings.responseGroup": "large",
        "callInfo.responseDataFormat": "JSON",
        "callInfo.apiKey": KEY,
    }
    # Another country's catalogue is another question.
    british = Farnell(KEY, store="uk.farnell.com")
    assert farnell.asks(YAGEO) != british.asks(YAGEO)
    assert farnell.asks(Query(" rc0603fr-0710kl ")) == farnell.asks(YAGEO)


def test_farnell_s_answer_is_read_as_an_offer(farnell, farnell_server):
    offer = farnell.read(farnell.fetch(YAGEO).body, YAGEO, NOBODY)

    assert (offer.supplier, offer.mpn, offer.manufacturer, offer.sku) == (
        "farnell",
        "RC0603FR-0710KL",
        "YAGEO",
        "9238603",
    )
    assert offer.description == "SMD Chip Resistor, 10 kohm, ± 1%, 100 mW, 0603"
    assert (offer.stock, offer.lifecycle, offer.status) == (518, "active", "Stocked")
    assert offer.prices == (
        PriceBreak(10, 0.0142),
        PriceBreak(100, 0.0079),
        PriceBreak(500, 0.0052),
    )
    assert offer.price_at(1) is None, "it is not sold by the piece"
    assert (offer.currency, offer.minimum, offer.lead_weeks) == ("EUR", 10, 9)
    assert offer.url == "https://de.farnell.com/search?st=9238603"
    assert offer.datasheet == "https://www.farnell.com/datasheets/3437310.pdf"


def test_farnell_s_own_page_is_linked_where_the_answer_gives_it(farnell):
    page = "https://de.farnell.com/yageo/rc0603fr-0710kl/dp/9238603"
    body = json.dumps({"x": {"products": [farnell_product(productURL=page)]}})

    assert farnell.read(body, YAGEO, NOBODY).url == page


def test_one_number_in_several_forms_is_shown_as_the_one_to_buy_a_few_of(farnell):
    products = [
        farnell_product(sku="reel", translatedMinimumOrderQuality=5000),
        farnell_product(sku="tape", translatedMinimumOrderQuality=10, packageName="Cut Tape"),
        farnell_product(sku="unpriced", translatedMinimumOrderQuality=1, prices=[]),
        farnell_product("RC0603FR-0710KLX", sku="another part"),
    ]
    body = json.dumps({"manufacturerPartNumberSearchReturn": {"products": products}})

    offer = farnell.read(body, YAGEO, NOBODY)

    assert (offer.sku, offer.packaging, offer.minimum) == ("tape", "Cut Tape", 10)


@pytest.mark.parametrize(
    "status,release,stock,lifecycle",
    [
        ("STOCKED", 4, 1, "active"),
        ("DIRECT_SHIP", 2, -7, "active"),
        ("defaultStatus", -1, 1, "unknown"),
        ("STOCKED", 6, 1, "nrnd"),  # to be discontinued
        ("NO_LONGER_STOCKED", 7, -4, "unknown"),  # Farnell's decision, not the maker's
        ("NO_LONGER_MANUFACTURED", 7, -3, "obsolete"),
        ("defaultStatus", -1, -3, "obsolete"),
    ],
)
def test_farnell_s_standing_in_the_library_s_words(status, release, stock, lifecycle):
    assert farnell_module.lifecycle_of(status, release=release, stock=stock) == lifecycle


def test_farnell_saying_nothing_in_particular_is_shown_as_nothing(farnell):
    body = json.dumps({"x": {"products": [farnell_product(productStatus="defaultStatus")]}})

    assert farnell.read(body, YAGEO, NOBODY).status == ""


@pytest.mark.parametrize(
    "answer",
    ["", "not json", "[]", '{"manufacturerPartNumberSearchReturn": {"numberOfResults": 0}}'],
)
def test_an_answer_from_farnell_that_is_not_one_says_nothing(farnell, answer):
    assert farnell.read(answer, YAGEO, NOBODY) is None


@pytest.mark.parametrize(
    "store,currency",
    [
        ("de.farnell.com", "EUR"),
        ("uk.farnell.com", "GBP"),
        ("ch.farnell.com", "CHF"),
        ("www.newark.com", "USD"),
        ("vn.element14.com", ""),  # not known here: nothing is better than a guess
    ],
)
def test_the_currency_is_the_store_s(store, currency):
    assert currency_of(store) == currency
    assert Farnell(KEY, store=store)._currency == currency
    assert Farnell(KEY, store=store, currency="sek")._currency == "SEK", "unless it is said"


def test_a_key_farnell_does_not_know_is_turned_away_with_the_reason(farnell_server):
    wrong = Farnell("not-the-key-1234", transport=httpx.MockTransport(farnell_server))

    with pytest.raises(SupplierError) as refused:
        wrong.fetch(YAGEO)

    said = str(refused.value)
    assert "HTTP 403: Account Inactive (ERR_403_DEVELOPER_INACTIVE)" in said
    assert "FARNELL_API_KEY" in said and "not-the-key-1234" not in said
    assert not isinstance(refused.value, TooManyCalls)


def test_farnell_saying_too_many_calls_is_said_as_that(farnell, farnell_server):
    farnell_server.gateway = "ERR_403_DEVELOPER_OVER_QPS"
    with pytest.raises(TooManyCalls) as second:
        farnell.fetch(YAGEO)
    assert second.value.retry_after == 2.0, "so many a second: it answers again at once"

    farnell_server.gateway = "ERR_403_DEVELOPER_OVER_RATE"
    with pytest.raises(TooManyCalls) as day:
        farnell.fetch(YAGEO)
    assert day.value.retry_after is None, "so many a day: for the service to wait out"


def test_nothing_farnell_says_or_fails_with_gives_the_key_away(farnell, farnell_server):
    farnell_server.down = True
    with pytest.raises(SupplierError) as unreachable:
        farnell.fetch(YAGEO)
    assert KEY not in str(unreachable.value)

    farnell_server.down = False
    farnell_server.answer = (
        500,
        '{"Fault": {"faultstring": "Could Not Query", "detail": {"searchException": '
        '{"exceptionCode": "200002", "exceptionString": "Could Not Query for the keyword"}}}}',
    )
    fetched = farnell.fetch(YAGEO)
    assert (fetched.status, fetched.answered) == (500, False)
    assert fetched.said == "Could Not Query Could Not Query for the keyword"


# -- asking several suppliers, each as often as it allows ---------------------------


@pytest.fixture()
def store(tmp_path):
    return SupplierStore(Database(tmp_path / "library.db"))


def test_a_refusal_that_looks_like_an_answer_is_not_kept(mouser_server, store, clock):
    wrong = Mouser("not-the-key-1234", transport=httpx.MockTransport(mouser_server))
    supply = Supply([wrong], store, limits={"mouser": 100}, holds_for_seconds=DAY, clock=clock)

    (answer,) = supply.ask(YAGEO, NOBODY)

    assert answer.state is State.FAILED, "not: Mouser does not list it"
    assert store.get("mouser", wrong.asks(YAGEO)) is None
    (used,) = supply.usage()
    assert "Invalid unique identifier" in used.failure and used.keys == ("MOUSER_API_KEY",)


def test_a_supplier_that_says_too_many_calls_is_left_alone_altogether(
    farnell, farnell_server, store, clock
):
    supply = Supply([farnell], store, limits={"farnell": 100}, holds_for_seconds=DAY, clock=clock)
    farnell_server.gateway = "ERR_403_DEVELOPER_OVER_RATE"

    (refused,) = supply.ask(YAGEO, NOBODY)
    supply.ask(Query("ANOTHER"), NOBODY)

    assert refused.state is State.OVER_LIMIT
    assert len(farnell_server.requests) == 1, "nothing more goes to it, whatever is asked"
    assert "called too often" in supply.usage()[0].failure

    farnell_server.gateway = None
    clock.pass_(RETRY_AFTER_SECONDS + 1)
    (answered,) = supply.ask(YAGEO, NOBODY)
    assert answered.state is State.FOUND


def test_a_supplier_is_not_called_more_often_than_it_allows(mouser, mouser_server, store, clock):
    """Three a minute, here. The fourth within the minute is not sent, and is
    not counted against the day."""
    supply = Supply(
        [mouser],
        store,
        limits={"mouser": 100},
        rates={"mouser": Rate(3, 60.0)},
        holds_for_seconds=DAY,
        clock=clock,
        sleep=clock.pass_,
    )
    for name in ("A", "B", "C"):
        supply.ask(Query(name), NOBODY)
        clock.pass_(10)

    (held,) = supply.ask(Query("D"), NOBODY)

    assert held.state is State.OVER_LIMIT
    assert mouser_server.searches == ["A", "B", "C"]
    assert store.calls("mouser", clock.now) == 3, "a call that was not made is not counted"

    clock.pass_(31)  # the first of the three is now a minute old
    supply.ask(Query("D"), NOBODY)
    assert mouser_server.searches == ["A", "B", "C", "D"]


def test_a_short_wait_for_a_supplier_s_allowance_is_waited_out(
    farnell, farnell_server, store, clock
):
    """Two a second: the third waits for the second to be up."""
    supply = Supply(
        [farnell],
        store,
        limits={"farnell": 100},
        rates={"farnell": Rate(2, 1.0)},
        holds_for_seconds=DAY,
        clock=clock,
        sleep=clock.pass_,
    )
    started = clock.now

    answers = [supply.ask(Query(name), NOBODY)[0] for name in ("A", "B", "C")]

    assert len(farnell_server.requests) == 3
    assert 0 < clock.now - started <= PATIENCE_SECONDS, "waited, and not for long"
    assert {answer.state for answer in answers} == {State.NOT_FOUND}


def test_each_supplier_is_asked_and_answers_for_itself(
    mouser, mouser_server, farnell, farnell_server, store, clock
):
    digikey_server = FakeDigiKey()
    from app.suppliers.digikey import DigiKey

    digikey = DigiKey(
        "the-client", "s3cret", transport=httpx.MockTransport(digikey_server), clock=clock
    )
    supply = Supply(
        [digikey, mouser, farnell],
        store,
        limits={"digikey": 10, "mouser": 10, "farnell": 10},
        holds_for_seconds=DAY,
        clock=clock,
    )
    farnell_server.products = []  # Farnell does not list it

    answers = supply.ask(YAGEO, NOBODY)
    supply.ask(YAGEO, NOBODY)

    assert [(answer.name, answer.state) for answer in answers] == [
        ("DigiKey", State.FOUND),
        ("Mouser", State.FOUND),
        ("Farnell", State.NOT_FOUND),
    ]
    assert [answer.offer.sku for answer in answers[:2]] == [
        "311-10.0KHRCT-ND",
        "603-RC0603FR-0710KL",
    ]
    assert (len(digikey_server.searches), len(mouser_server.requests)) == (1, 1), "once each"
    assert len(farnell_server.requests) == 1
    assert [used.calls_today for used in supply.usage()] == [1, 1, 1]


# -- from the environment ---------------------------------------------------------


def test_mouser_and_farnell_are_set_up_from_the_environment():
    settings = Settings.from_env(
        {
            **ENVIRONMENT,
            "MOUSER_API_KEY": " mouser-key ",
            "MOUSER_DAILY_LIMIT": "500",
            "FARNELL_API_KEY": "farnell-key",
            "FARNELL_STORE": "DE.farnell.com",
            "FARNELL_CURRENCY": "eur",
            "FARNELL_DAILY_LIMIT": "250",
        }
    )

    assert (settings.mouser_api_key, settings.mouser_daily_limit) == ("mouser-key", 500)
    assert (settings.farnell_api_key, settings.farnell_store, settings.farnell_currency) == (
        "farnell-key",
        "de.farnell.com",
        "EUR",
    )
    assert settings.farnell_daily_limit == 250

    usage = {used.id: used for used in create_app(settings).state.supply.usage()}
    assert list(usage) == ["digikey", "mouser", "farnell"]
    assert (usage["digikey"].configured, usage["mouser"].configured) == (False, True)
    assert (usage["mouser"].limit, usage["farnell"].limit) == (500, 250)

    bare = Settings.from_env(ENVIRONMENT)
    assert (bare.mouser_api_key, bare.farnell_api_key, bare.farnell_currency) == (None, None, None)
    assert (bare.farnell_store, bare.mouser_daily_limit) == ("uk.farnell.com", 1000)


@pytest.mark.parametrize("name", ["MOUSER_API_URL", "FARNELL_API_URL"])
def test_a_key_is_not_sent_to_an_address_that_is_not_https(name):
    problems = Settings.from_env({**ENVIRONMENT, name: "http://example.com"}).problems()

    assert any(f"{name} must be an https address" in problem for problem in problems)


def test_the_status_page_says_which_setting_each_supplier_waits_for(client):
    page = " ".join(client.get("/").text.split())

    assert "<strong>Mouser</strong>" in page and "<strong>Farnell</strong>" in page
    assert '<span class="mono">MOUSER_API_KEY</span> is set' in page
    assert '<span class="mono">FARNELL_API_KEY</span> is set' in page
    assert (
        '<span class="mono">DIGIKEY_CLIENT_ID</span> and '
        '<span class="mono">DIGIKEY_CLIENT_SECRET</span> are set' in page
    )


def test_a_request_s_address_is_not_written_to_the_log(mouser, capsys):
    """httpx logs every request with its whole address, and the key is in it."""
    configure_logging("INFO")
    try:
        mouser.fetch(YAGEO)
        logging.getLogger("app.test").info("something that is logged")
    finally:
        logged = capsys.readouterr().out
        logging.getLogger().handlers.clear()

    assert "something that is logged" in logged
    assert KEY not in logged and "apiKey" not in logged
