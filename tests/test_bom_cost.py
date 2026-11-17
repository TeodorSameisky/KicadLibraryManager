"""What a BOM costs to build, from what the suppliers say.

The suppliers here are made up: each sells what it is told to, at the prices
it is told, and counts how often it is asked. What is exercised is the
costing -- what is bought, where, and what it comes to -- and that working it
out asks nobody.
"""

import csv
import io
import json

import pytest

from app.boms.parse import parse_bom
from app.boms.store import BomStore
from app.db.database import Database
from app.domain.costing import ENOUGH, SHORT, UNKNOWN, choose, purchase
from app.domain.supply import Offer, PriceBreak
from app.services import costing
from app.services.supply import Rate, Supply
from app.suppliers.base import Fetched
from app.suppliers.store import SupplierStore
from app.web.templating import money
from tests.conftest import sign_in
from tests.support import MPN
from tests.test_suppliers import Clock

DAY = 24 * 3600


def offer(supplier="a", breaks=((1, 0.10), (10, 0.05), (100, 0.02)), **more):
    return Offer(
        supplier=supplier,
        mpn="X",
        manufacturer="Maker",
        prices=tuple(PriceBreak(quantity, price) for quantity, price in breaks),
        currency=more.pop("currency", "EUR"),
        stock=more.pop("stock", 10_000),
        **more,
    )


# -- buying one line --------------------------------------------------------------


def test_what_is_needed_is_bought_at_the_price_for_that_many():
    bought = purchase(offer(), 30, mpn="X")

    assert (bought.quantity, bought.unit_price, bought.cost, bought.surplus) == (
        30,
        0.05,
        1.5,
        0,
    )
    assert purchase(offer(), 100, mpn="X").unit_price == 0.02, "the next break is reached"


def test_fewer_than_a_supplier_sells_is_bought_as_the_fewest_it_does():
    by_minimum = purchase(offer(minimum=50), 3, mpn="X")
    by_first_break = purchase(offer(breaks=((10, 0.0142), (100, 0.0079))), 3, mpn="X")

    assert (by_minimum.quantity, by_minimum.surplus, by_minimum.unit_price) == (50, 47, 0.05)
    assert (by_first_break.quantity, by_first_break.unit_price) == (10, 0.0142)


def test_an_offer_without_prices_cannot_be_bought_from():
    assert purchase(offer(breaks=()), 3, mpn="X") is None


@pytest.mark.parametrize(
    "stock,expected", [(500, ENOUGH), (30, ENOUGH), (29, SHORT), (None, UNKNOWN)]
)
def test_whether_a_supplier_has_enough(stock, expected):
    assert purchase(offer(stock=stock), 30, mpn="X").stock == expected


def test_a_supplier_with_enough_is_chosen_before_a_cheaper_one_without():
    cheap_but_short = purchase(offer("cheap", breaks=((1, 0.01),), stock=5), 30, mpn="X")
    saying_nothing = purchase(offer("quiet", breaks=((1, 0.02),), stock=None), 30, mpn="X")
    stocked = purchase(offer("stocked", breaks=((1, 0.03),)), 30, mpn="X")

    assert choose([cheap_but_short, saying_nothing, stocked], "EUR").offer.supplier == "stocked"
    assert choose([cheap_but_short, saying_nothing], "EUR").offer.supplier == "quiet"
    assert choose([cheap_but_short], "EUR").offer.supplier == "cheap", "better than nothing"
    assert choose([], "EUR") is None


def test_the_cheapest_in_the_costing_s_currency_is_chosen():
    dollars = purchase(offer("us", breaks=((1, 0.01),), currency="USD"), 10, mpn="X")
    euros = purchase(offer("de", breaks=((1, 0.09),)), 10, mpn="X")
    cheaper_euros = purchase(offer("fr", breaks=((1, 0.08),)), 10, mpn="X")

    assert (
        choose([dollars, euros, cheaper_euros], "EUR").offer.supplier == "fr"
    ), "a dollar price is not compared with a euro price"
    assert choose([dollars, euros], "USD").offer.supplier == "us"


def test_at_the_same_cost_the_preferred_source_is_chosen():
    alternate = purchase(offer("a"), 10, mpn="ALT", preferred=False)
    preferred = purchase(offer("b"), 10, mpn="PREF", preferred=True)

    assert choose([alternate, preferred], "EUR").mpn == "PREF"


# -- a library, a BOM, and suppliers to ask ------------------------------------------


class Seller:
    """A supplier that sells what it is given: {mpn: offer fields}."""

    def __init__(self, id, catalogue, configured=True):
        self.id = id
        self.name = id.title()
        self.keys = (f"{id.upper()}_KEY",)
        self.configured = configured
        self.catalogue = catalogue
        self.asked = []

    def asks(self, query):
        return f"part {query.mpn.upper()}"

    def fetch(self, query):
        self.asked.append(query.mpn)
        listed = self.catalogue.get(query.mpn)
        return Fetched(status=200 if listed else 404, body=json.dumps(listed or {}))

    def read(self, body, query, manufacturers):
        listed = json.loads(body)
        if not listed:
            return None
        return Offer(
            supplier=self.id,
            mpn=query.mpn,
            manufacturer=query.manufacturer,
            sku=f"{self.id}-{query.mpn}",
            prices=tuple(PriceBreak(q, p) for q, p in listed["prices"]),
            currency=listed.get("currency", "EUR"),
            stock=listed.get("stock"),
            minimum=listed.get("minimum"),
            status=listed.get("status", ""),
            lifecycle=listed.get("lifecycle", "active"),
        )


def selling(*prices, **more):
    return {"prices": [list(step) for step in prices], "stock": 100_000, **more}


TWO_SOURCES = """\
ipn: 1102-0002
description: Resistor 4k7 1% 0.1W 0603
status: approved
symbol: Passives:R_0603
footprint: Passives:R_0603_1608Metric
mpns:
  - mpn: CRCW
    preferred: true
  - mpn: RC0603FR-0710KL
"""

NO_SOURCE = """\
ipn: 1102-0003
description: Resistor nobody sells yet
status: draft
symbol: Passives:R_0603
footprint: Passives:R_0603_1608Metric
"""

BOM = """\
Reference,Value,IPN,DNP
"R1,R2",10k,1102-0001,
R3,4k7,1102-0002,
R4,?,1102-0003,
R5,?,1102-0009,
H1,MountingHole,,
R6,10k,1102-0001,DNP
"""


@pytest.fixture()
def library(tree):
    tree.standard()
    tree.write("mpns/CRCW.yaml", MPN.replace("RC0603FR-0710KL", "CRCW").replace("Yageo", "Vishay"))
    tree.write("parts/1102/1102-0002.yaml", TWO_SOURCES)
    tree.write("parts/1102/1102-0003.yaml", NO_SOURCE)
    return tree.snapshot()


@pytest.fixture()
def bom(tmp_path):
    store = BomStore(tmp_path / "boms.db")
    return store.save(parse_bom(BOM), project="Amplifier", revision="A")


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def sellers():
    return (
        Seller(
            "north",
            {
                "RC0603FR-0710KL": selling((1, 0.10), (10, 0.05), (100, 0.01)),
                "CRCW": selling((1, 0.20)),
            },
        ),
        Seller(
            "south",
            {
                "RC0603FR-0710KL": selling((10, 0.03), (100, 0.008), minimum=10),
                "CRCW": selling((1, 0.30)),
            },
        ),
    )


@pytest.fixture()
def supply(sellers, tmp_path, clock):
    return Supply(
        sellers,
        SupplierStore(Database(tmp_path / "suppliers.db")),
        limits={"north": 100, "south": 100},
        holds_for_seconds=DAY,
        clock=clock,
    )


def by_item(worked_out):
    """The lines, by their first reference. They are in the board's order --
    H1 before R1 -- and not the file's."""
    return {row.line.references[0]: row for row in worked_out.lines}


def test_working_out_the_cost_asks_nobody(bom, library, supply, sellers):
    worked_out = costing.cost_bom(bom, library, supply, boards=10)

    assert [seller.asked for seller in sellers] == [[], []]
    assert (worked_out.questions, worked_out.unasked) == (4, 4), "two parts, two suppliers"
    lines = by_item(worked_out)
    assert lines["R1"].note == costing.NOT_ASKED
    assert worked_out.priced == 0 and worked_out.totals == ()


def test_asking_fills_in_the_cost_and_what_was_asked_is_not_asked_again(
    bom, library, supply, sellers
):
    assert costing.ask_for_bom(bom, library, supply) == 2
    assert costing.ask_for_bom(bom, library, supply) == 0, "everything is kept"
    assert [sorted(seller.asked) for seller in sellers] == [
        ["CRCW", "RC0603FR-0710KL"],
        ["CRCW", "RC0603FR-0710KL"],
    ]

    worked_out = costing.cost_bom(bom, library, supply, boards=10)
    assert worked_out.unasked == 0

    lines = by_item(worked_out)
    # R1, R2 on ten boards: twenty. South sells from ten, at 0.03 each.
    first = lines["R1"]
    assert (first.needed, first.bought.offer.supplier, first.bought.quantity) == (20, "south", 20)
    assert first.bought.unit_price == 0.03
    # R3 on ten boards: ten. Its preferred source costs 0.20 at North; its
    # alternate 0.03 at South. The alternate is cheaper, and approved.
    second = lines["R3"]
    assert (second.bought.mpn, second.bought.preferred, second.bought.offer.supplier) == (
        "RC0603FR-0710KL",
        False,
        "south",
    )
    assert {ref: lines[ref].note for ref in ("R4", "R5", "H1", "R6")} == {
        "R4": costing.NO_SOURCE,
        "R5": costing.NOT_IN_LIBRARY,
        "H1": costing.NOT_A_LIBRARY_PART,
        "R6": costing.NOT_FITTED,
    }
    assert (worked_out.priced, len(worked_out.fitted), worked_out.complete) == (2, 5, False)
    assert worked_out.currency == "EUR"
    assert worked_out.totals == (("EUR", pytest.approx(0.9)),)
    assert worked_out.per_board == pytest.approx(0.09)


def test_one_board_buys_the_fewest_that_can_be_bought(bom, library, supply):
    costing.ask_for_bom(bom, library, supply)

    lines = by_item(costing.cost_bom(bom, library, supply, boards=1))

    # Two at North cost 0.20; South sells no fewer than ten, at 0.30.
    assert (lines["R1"].bought.offer.supplier, lines["R1"].bought.quantity) == ("north", 2)
    assert lines["R1"].bought.cost == pytest.approx(0.2)


def test_a_part_no_supplier_lists_says_so(bom, library, supply, sellers):
    for seller in sellers:
        seller.catalogue.pop("CRCW")
        seller.catalogue.pop("RC0603FR-0710KL")
    costing.ask_for_bom(bom, library, supply)

    lines = by_item(costing.cost_bom(bom, library, supply))

    assert (lines["R1"].note, lines["R3"].note) == (costing.NOT_LISTED, costing.NOT_LISTED)


def test_without_a_supplier_nothing_is_priced_and_nobody_is_asked(bom, library, tmp_path):
    nobody = Supply(
        [Seller("north", {}, configured=False)],
        SupplierStore(Database(tmp_path / "suppliers.db")),
        limits={},
        holds_for_seconds=DAY,
    )

    worked_out = costing.cost_bom(bom, library, nobody)

    assert costing.ask_for_bom(bom, library, nobody) == 0
    assert (worked_out.suppliers, worked_out.questions) == (False, 0)
    assert by_item(worked_out)["R1"].note == costing.NO_SUPPLIER


def test_the_preferred_sources_are_asked_about_first_and_asking_stops_in_time(
    bom, library, supply, sellers
):
    ticks = iter(range(100))

    asked = costing.ask_for_bom(bom, library, supply, seconds=1.5, clock=lambda: next(ticks))

    assert asked == 1
    assert [seller.asked for seller in sellers] == [
        ["RC0603FR-0710KL"],
        ["RC0603FR-0710KL"],
    ], "1102-0001's only source is preferred; 1102-0002's preferred CRCW comes next"


def test_a_supplier_that_will_not_be_asked_now_is_left_for_later(bom, library, sellers, tmp_path):
    clock = Clock()
    supply = Supply(
        sellers,
        SupplierStore(Database(tmp_path / "suppliers.db")),
        limits={"north": 100, "south": 100},
        rates={"south": Rate(1, 60.0)},
        holds_for_seconds=DAY,
        clock=clock,
        sleep=clock.pass_,
    )

    costing.ask_for_bom(bom, library, supply)
    first = costing.cost_bom(bom, library, supply)
    costing.ask_for_bom(bom, library, supply)
    again = costing.cost_bom(bom, library, supply)

    assert (first.unasked, again.unasked) == (1, 1), "South allows one a minute"
    assert sellers[1].asked == ["RC0603FR-0710KL"]

    clock.pass_(61)
    costing.ask_for_bom(bom, library, supply)
    assert costing.cost_bom(bom, library, supply).unasked == 0


def test_an_old_price_is_used_and_said_to_be_old(bom, library, supply, clock):
    costing.ask_for_bom(bom, library, supply)
    clock.pass_(DAY + 1)

    worked_out = costing.cost_bom(bom, library, supply)

    assert worked_out.unasked == 4, "every answer is old: worth asking again"
    assert worked_out.priced == 2 and by_item(worked_out)["R1"].bought.old


def test_a_line_is_bought_in_the_costing_s_currency_where_it_can_be(bom, library, supply, sellers):
    """R3's preferred source is a cent in dollars; its alternate ten cents in
    euros. Without a rate the two cannot be compared, and the costing is in
    euros: most of its prices are."""
    sellers[0].catalogue.update(
        {"RC0603FR-0710KL": selling((1, 0.10)), "CRCW": selling((1, 0.01), currency="USD")}
    )
    sellers[1].catalogue.clear()
    costing.ask_for_bom(bom, library, supply)

    worked_out = costing.cost_bom(bom, library, supply)

    assert worked_out.currency == "EUR"
    assert by_item(worked_out)["R3"].bought.mpn == "RC0603FR-0710KL"
    assert worked_out.totals == (("EUR", pytest.approx(0.3)),)


def test_a_line_only_priced_in_another_currency_is_a_total_of_its_own(
    bom, library, tree, supply, sellers
):
    only_preferred = TWO_SOURCES.replace("  - mpn: RC0603FR-0710KL\n", "")
    tree.write("parts/1102/1102-0002.yaml", only_preferred)
    library = tree.snapshot()
    sellers[0].catalogue.update(
        {"RC0603FR-0710KL": selling((1, 0.10)), "CRCW": selling((1, 0.01), currency="USD")}
    )
    sellers[1].catalogue.clear()
    costing.ask_for_bom(bom, library, supply)

    worked_out = costing.cost_bom(bom, library, supply)

    assert worked_out.currency == "EUR", "as many prices in each: the first by name"
    assert worked_out.totals == (("EUR", pytest.approx(0.2)), ("USD", pytest.approx(0.01)))
    assert worked_out.total == pytest.approx(0.2), "the total is the costing's currency's"


def test_a_supplier_that_has_the_part_as_obsolete_is_marked(bom, library, supply, sellers):
    sellers[0].catalogue["RC0603FR-0710KL"] = selling(
        (1, 0.001), lifecycle="obsolete", status="Obsolete"
    )
    costing.ask_for_bom(bom, library, supply)

    line = by_item(costing.cost_bom(bom, library, supply))["R1"]

    assert line.bought.offer.supplier == "north" and line.warns, "the library has it as active"


def test_the_number_of_boards_is_whole_and_at_least_one():
    assert costing.boards_of(["12"]) == 12
    assert [costing.boards_of([text]) for text in ("0", "-3", "", "lots", " 4 ")] == [1, 1, 1, 1, 4]
    assert costing.boards_of(["99999999999"]) == costing.MAX_BOARDS


def test_the_costing_downloads_line_by_line(bom, library, supply):
    costing.ask_for_bom(bom, library, supply)

    rows = list(csv.DictReader(io.StringIO(costing.cost_bom(bom, library, supply, 10).csv())))

    assert len(rows) == 6
    first = rows[1]  # H1 is first: the board's order
    assert (first["IPN"], first["Needed"], first["Supplier"], first["Order code"]) == (
        "1102-0001",
        "20",
        "South",
        "south-RC0603FR-0710KL",
    )
    assert (first["Buy"], first["Unit price"], first["Currency"], first["Line cost"]) == (
        "20",
        "0.03",
        "EUR",
        "0.60",
    )
    assert rows[0]["Note"] == costing.NOT_A_LIBRARY_PART
    assert rows[4]["Note"] == costing.NOT_IN_LIBRARY
    assert rows[5]["Note"] == costing.NOT_FITTED and rows[5]["Needed"] == ""


@pytest.mark.parametrize(
    "amount,exact,shown",
    [
        (1234.5, False, "1,234.50 EUR"),
        (0.0142, True, "0.0142 EUR"),
        (0.1, True, "0.10 EUR"),
        (12.3456789, True, "12.3457 EUR"),
    ],
)
def test_a_price_on_a_page(amount, exact, shown):
    assert money(amount, "EUR", exact=exact) == shown


# -- over the web ---------------------------------------------------------------------


@pytest.fixture()
def site(make_client, tree, library, sellers, supply):
    client = sign_in(make_client(tree))
    client.app.state.supply = supply
    uploaded = client.post(
        "/api/v1/boms", json={"project": "Amplifier", "revision": "A", "content": BOM}
    )
    client.bom_id = uploaded.json()["id"]
    return client


def test_the_cost_over_the_api_asks_nobody_until_asked_to(site, sellers):
    path = f"/api/v1/boms/{site.bom_id}/cost"

    before = site.get(path, params={"boards": 10}).json()
    assert (before["questions"], before["unasked"], before["priced"]) == (4, 4, 0)
    assert [seller.asked for seller in sellers] == [[], []]

    after = site.post(f"{path}/ask", params={"boards": 10}).json()
    assert (after["unasked"], after["priced"], after["fitted"]) == (0, 2, 5)
    assert after["totals"] == [
        {"currency": "EUR", "total": pytest.approx(0.9), "per_board": pytest.approx(0.09)}
    ]
    first = after["lines"][1]  # R1, R2: H1 comes first
    assert first["bought"]["name"] == "South" and first["bought"]["in_stock"] == "enough"
    assert first["bought"]["quantity"] == 20 and first["bought"]["surplus"] == 0

    assert site.get(path, params={"boards": 0}).status_code == 422


def test_the_cost_page_and_its_download(site):
    site.post(f"/api/v1/boms/{site.bom_id}/cost/ask")

    page = site.get(f"/boms/{site.bom_id}/cost", params={"boards": "10"}).text
    flat = " ".join(page.split())
    assert "Cost of Amplifier" in page and 'value="10"' in page
    assert "0.90 EUR" in flat and "0.09 EUR" in flat and "2 of 5" in flat
    assert 'id="cost-ask"' in page and "bom-cost.js" in page
    # Worked out again as the number changes: there is nothing to press.
    assert 'type="submit"' not in page and "Work it out" not in page
    assert (
        'cost.csv?boards=10"' in page.split('id="costing"', 1)[1]
    ), "the download is replaced with the costing, so it is for the number on screen"
    assert "4 of 4 supplier answers are here." in flat
    assert 'id="cost-ask-start" hidden' in page, "nothing is left to ask"

    assert 'value="1"' in site.get(f"/boms/{site.bom_id}/cost", params={"boards": "lots"}).text

    download = site.get(f"/api/v1/boms/{site.bom_id}/cost.csv", params={"boards": 10})
    assert (
        download.headers["content-disposition"] == 'attachment; filename="Amplifier-A-cost-10.csv"'
    )
    assert "1102-0001" in download.text

    assert f'href="/boms/{site.bom_id}/cost"' in site.get(f"/boms/{site.bom_id}").text


def test_the_cost_needs_a_session(client):
    assert client.get("/api/v1/boms/1/cost").status_code == 401
    assert client.post("/api/v1/boms/1/cost/ask").status_code == 401


def test_the_cost_page_without_a_supplier_still_follows_the_number_of_boards(
    make_client, tree, library, tmp_path
):
    client = sign_in(make_client(tree))
    client.app.state.supply = Supply(
        [Seller("north", {}, configured=False)],
        SupplierStore(Database(tmp_path / "suppliers.db")),
        limits={},
        holds_for_seconds=DAY,
    )
    bom_id = client.post(
        "/api/v1/boms", json={"project": "Amplifier", "revision": "A", "content": BOM}
    ).json()["id"]

    page = client.get(f"/boms/{bom_id}/cost").text

    assert "No supplier is set up" in page and 'id="cost-ask"' not in page
    assert "bom-cost.js" in page and 'id="cost-boards"' in page
