"""Choosing a part by what it says about itself: 10k, 1%, 0603."""

import pytest

from app.domain.parameters import Condition, Op, satisfied
from app.domain.quantities import in_order, magnitude
from app.errors import Invalid
from app.services import search
from tests.conftest import sign_in

# -- a value as a number --------------------------------------------------------


@pytest.mark.parametrize(
    "written,amount",
    [
        ("10k", 10e3),
        ("10K", 10e3),
        ("4k7", 4.7e3),
        ("4R7", 4.7),
        ("0R1", 0.1),
        ("R47", 0.47),
        ("100R", 100),
        ("0R", 0),
        ("2M2", 2.2e6),
        ("100n", 100e-9),
        ("0.1uF", 100e-9),
        ("10 µF", 10e-6),
        ("10 kΩ", 10e3),
        ("4,7k", 4.7e3),
        ("1%", 1),
        ("±5%", 5),
        ("+/-0.1%", 0.1),
        ("50V", 50),
        ("3V3", 3.3),
        ("100mW", 0.1),
        ("1/4W", 0.25),
        ("1/16 W", 0.0625),
        ("-40°C", -40),
        ("0603", 603),
        ("100nF 50V X7R", 100e-9),  # the first of several
    ],
)
def test_a_value_as_it_is_written_is_a_number(written, amount):
    assert magnitude(written) == pytest.approx(amount)


@pytest.mark.parametrize(
    "written", ["", "  ", "X7R", "1N4148", "2N7002", "74HC595", "SOT-23", "C0G", "n/a", "1/0W"]
)
def test_what_is_not_a_quantity_has_no_magnitude(written):
    assert magnitude(written) is None


def test_values_are_put_in_order_by_what_they_stand_for():
    assert in_order(["100k", "4k7", "X7R", "10k", "1M", "470R", "C0G", "0R"]) == [
        "0R",
        "470R",
        "4k7",
        "10k",
        "100k",
        "1M",
        "C0G",
        "X7R",
    ], "not by their letters, which would put 100k before 4k7"


# -- a condition ----------------------------------------------------------------


@pytest.mark.parametrize(
    "written,read",
    [
        ("Resistance=10k", ("Resistance", Op.IS, "10k")),
        (" Temperature coefficient = X7R ", ("Temperature coefficient", Op.IS, "X7R")),
        ("Voltage>=25V", ("Voltage", Op.AT_LEAST, "25V")),
        ("Power<=1/4W", ("Power", Op.AT_MOST, "1/4W")),
        ("Note=a=b", ("Note", Op.IS, "a=b")),
    ],
)
def test_a_condition_is_a_name_a_sign_and_a_value(written, read):
    condition = Condition.parse(written)
    assert (condition.name, condition.op, condition.value) == read


@pytest.mark.parametrize(
    "written,said",
    [
        ("Resistance", "needs =, >= or <="),
        ("=10k", "does not say which parameter"),
        ("Resistance=", "without a value"),
        ("Dielectric>=X7R", "is not a quantity"),
    ],
)
def test_a_condition_that_says_nothing_is_refused_in_a_sentence(written, said):
    with pytest.raises(ValueError, match=said):
        Condition.parse(written)


def test_what_a_part_says_is_held_against_the_conditions():
    def meets(values, *conditions):
        return satisfied(values, [Condition.parse(text) for text in conditions])

    assert meets(["10k"], "R=10k")
    assert not meets(["10K"], "R=10k"), "what it is, is as it is written"
    assert meets(["0603"], "P=0402", "P=0603"), "two that say what it is are a choice"
    assert not meets([], "R=10k") and not meets([], "R>=1k"), "a part that does not say"
    assert meets(["4k7"], "R>=4700") and meets(["4k7"], "R<=4.7k"), "the bound is included"
    assert meets(["10 kΩ"], "R>=1k", "R<=10k") and not meets(["10k"], "R>=1k", "R<=9k9")
    assert not meets(["X7R"], "D>=1"), "what is not a quantity is not at least anything"
    assert meets(["Yageo", "Vishay"], "M=Vishay"), "any one of several answers will do"
    assert meets(["anything"]), "nothing asked"


# -- a library to search --------------------------------------------------------

TEMPLATE = """\
value: '{Resistance}'
fields:
  - name: Resistance
    required: true
  - name: Tolerance
    options: [0.1%, 1%, 5%]
  - name: Power
"""


def resistor(ipn, resistance, tolerance="1%", power="0.1W", size="0603", mpn="RC0603FR-0710KL"):
    return f"""\
ipn: {ipn}
description: Resistor {resistance} {tolerance} {power} {size}
status: approved
symbol: Passives:R_0603
footprint: Passives:R_{size}
fields:
  Resistance: {resistance}
  Tolerance: {tolerance}
  Power: {power}
mpns:
  - mpn: {mpn}
    preferred: true
"""


@pytest.fixture()
def resistors(tree):
    """Five resistors and a capacitor.

    1102-0001  10k   1%    0.1W    0603   Yageo
    1102-0002  4k7   1%    0.1W    0603   Yageo
    1102-0003  100k  5%    0.1W    0603   Vishay
    1102-0004  10k   0.1%  1/4W    1206   Yageo, Vishay
    1102-0005  470R  5%    100mW   0402   Yageo
    """
    tree.standard().write("templates/1102.yaml", TEMPLATE)
    tree.write("mpns/CRCW.yaml", "mpn: CRCW\nmanufacturer: Vishay\nlifecycle: active\n")
    tree.write("parts/1102/1102-0001.yaml", resistor("1102-0001", "10k"))
    tree.write("parts/1102/1102-0002.yaml", resistor("1102-0002", "4k7"))
    tree.write("parts/1102/1102-0003.yaml", resistor("1102-0003", "100k", "5%", mpn="CRCW"))
    tree.write(
        "parts/1102/1102-0004.yaml",
        resistor("1102-0004", "10k", "0.1%", "1/4W", "1206").replace(
            "    preferred: true\n", "    preferred: true\n  - mpn: CRCW\n"
        ),
    )
    tree.write("parts/1102/1102-0005.yaml", resistor("1102-0005", "470R", "5%", "100mW", "0402"))
    tree.write(
        "parts/1101/1101-0001.yaml",
        "ipn: 1101-0001\ndescription: Capacitor 100n\nstatus: approved\n"
        "symbol: Passives:R_0603\nfootprint: Passives:R_0603\nfields:\n  Value: 100n\n",
    )
    return tree


def found(snapshot, *where, **more):
    return [part.ipn for part in search.find(snapshot, where=where, **more).parts]


def offered(snapshot, *where, **more):
    """Each parameter offered, with its values and what each would leave."""
    result = search.find(snapshot, category="1102", where=where, **more)
    return {
        parameter.name: {choice.value: choice.count for choice in parameter.choices}
        for parameter in result.parameters
    }


def test_parts_are_narrowed_by_what_they_say(resistors):
    snapshot = resistors.snapshot()

    assert found(snapshot, "Resistance=10k") == ["1102-0001", "1102-0004"]
    assert found(snapshot, "Resistance=10k", "Tolerance=1%") == ["1102-0001"]
    assert found(snapshot, "Tolerance=0.1%", "Tolerance=5%") == [
        "1102-0003",
        "1102-0004",
        "1102-0005",
    ], "one or the other"
    assert found(snapshot, "Resistance>=4k7", "Resistance<=10k") == [
        "1102-0001",
        "1102-0002",
        "1102-0004",
    ]
    assert found(snapshot, "Power>=0.2W") == ["1102-0004"], "1/4W is at least 0.2W"
    assert found(snapshot, "Power<=0.1W") == [
        "1102-0001",
        "1102-0002",
        "1102-0003",
        "1102-0005",
    ], "100mW is 0.1W"
    assert found(snapshot, "Resistance=10k", query="1206") == ["1102-0004"], "with words as well"
    assert found(snapshot, "Resistance=1M") == []


def test_the_footprint_and_the_maker_are_parameters_without_being_fields(resistors):
    snapshot = resistors.snapshot()

    assert found(snapshot, "Footprint=R_1206") == ["1102-0004"], "without its library"
    assert found(snapshot, "Manufacturer=Vishay") == ["1102-0003", "1102-0004"]
    assert found(snapshot, "Manufacturer=Yageo", "Resistance=10k") == ["1102-0001", "1102-0004"]


def test_the_maker_goes_by_the_one_name_the_library_knows(resistors):
    resistors.write(
        "manufacturers.yaml", "- name: Vishay Intertechnology\n  aliases: [Vishay]\n- name: Yageo\n"
    )
    snapshot = resistors.snapshot()

    assert found(snapshot, "Manufacturer=Vishay Intertechnology") == ["1102-0003", "1102-0004"]
    assert "Vishay" not in offered(snapshot)["Manufacturer"]


def test_a_category_says_what_its_parts_can_be_narrowed_by(resistors):
    snapshot = resistors.snapshot()

    result = search.find(snapshot, category="1102")
    names = [parameter.name for parameter in result.parameters]

    assert names == [
        "Resistance",
        "Tolerance",
        "Power",
        "Footprint",
        "Manufacturer",
    ], "the template's fields as it has them, then what is not a field"
    assert offered(snapshot) == {
        "Resistance": {"470R": 1, "4k7": 1, "10k": 2, "100k": 1},
        "Tolerance": {"0.1%": 1, "1%": 2, "5%": 2},
        "Power": {"0.1W": 3, "100mW": 1, "1/4W": 1},
        "Footprint": {"R_0402": 1, "R_0603": 3, "R_1206": 1},
        "Manufacturer": {"Vishay": 2, "Yageo": 4},
    }
    assert list(offered(snapshot)["Resistance"]) == ["470R", "4k7", "10k", "100k"], "smallest first"
    numeric = {parameter.name: parameter.numeric for parameter in result.parameters}
    assert numeric == {
        "Resistance": True,
        "Tolerance": True,
        "Power": True,
        "Footprint": False,
        "Manufacturer": False,
    }


def test_a_value_counts_the_parts_the_other_conditions_leave(resistors):
    snapshot = resistors.snapshot()

    narrowed = offered(snapshot, "Resistance=10k")

    assert narrowed["Tolerance"] == {"0.1%": 1, "1%": 1}, "of the two 10k parts"
    assert narrowed["Resistance"] == {
        "470R": 1,
        "4k7": 1,
        "10k": 2,
        "100k": 1,
    }, "its own condition does not count against it: what choosing another would give"
    assert list(narrowed) == list(offered(snapshot)), "the same parameters, however narrowed"


def test_what_is_asked_for_stays_in_sight_even_when_nothing_has_it(resistors):
    snapshot = resistors.snapshot()

    assert offered(snapshot, "Resistance=1M")["Resistance"]["1M"] == 0
    # Asked of a parameter this category does not have at all.
    stray = search.find(snapshot, category="1102", where=["Colour=red"])
    assert stray.parts == []
    colour = next(p for p in stray.parameters if p.name == "Colour")
    assert [(c.value, c.count) for c in colour.choices] == [("red", 0)]


def test_a_parameter_with_one_value_is_not_offered(resistors):
    snapshot = resistors.snapshot()

    capacitors = search.find(snapshot, category="1101")

    assert [part.ipn for part in capacitors.parts] == ["1101-0001"]
    assert capacitors.parameters == (), "one capacitor: nothing to tell apart"


def test_outside_a_category_parts_are_narrowed_and_nothing_is_offered(resistors):
    result = search.find(resistors.snapshot(), where=["Value=100n"])

    assert [part.ipn for part in result.parts] == ["1101-0001"]
    assert result.parameters == ()


def test_a_search_that_cannot_be_read_names_each_thing_wrong(resistors):
    with pytest.raises(Invalid) as refused:
        search.find(resistors.snapshot(), where=["Resistance", "Dielectric>=X7R", "Power=0.1W"])

    assert len(refused.value.problems) == 2
    assert "needs =, >= or <=" in refused.value.problems[0]

    with pytest.raises(Invalid, match="At most"):
        search.find(resistors.snapshot(), where=["A=1"] * (search.MAX_CONDITIONS + 1))


# -- over the API ---------------------------------------------------------------


@pytest.fixture()
def site(make_client, resistors):
    return sign_in(make_client(resistors))


def test_the_part_list_takes_conditions_and_says_what_is_left_to_choose(site):
    answer = site.get(
        "/api/v1/parts",
        params={"category": "1102", "where": ["Resistance>=4k7", "Tolerance=1%"]},
    ).json()

    assert [part["ipn"] for part in answer["parts"]] == ["1102-0001", "1102-0002"]
    assert (answer["total"], answer["matched"], answer["returned"]) == (6, 2, 2)
    resistance, tolerance = answer["parameters"][:2]
    assert resistance == {
        "name": "Resistance",
        "numeric": True,
        "choices": [{"value": "4k7", "count": 1}, {"value": "10k", "count": 1}],
    }
    assert tolerance["choices"] == [
        {"value": "0.1%", "count": 1},
        {"value": "1%", "count": 2},
        {"value": "5%", "count": 1},
    ], "of the parts from 4k7 up"


def test_without_a_category_the_list_offers_no_parameters(site):
    answer = site.get("/api/v1/parts").json()

    assert answer["matched"] == 6 and answer["parameters"] == []


def test_a_condition_that_cannot_be_read_is_refused_with_the_reason(site):
    refused = site.get("/api/v1/parts", params={"where": "Resistance"})

    assert refused.status_code == 422
    assert "needs =, >= or <=" in refused.json()["problems"][0]


# -- in the catalog -------------------------------------------------------------


def test_the_catalog_has_a_place_for_the_parameters(site):
    page = site.get("/panel").text
    script = site.get("/static/js/panel.js").text

    assert 'id="parameters"' in page
    assert "data.parameters" in script and 'query.append("where"' in script
