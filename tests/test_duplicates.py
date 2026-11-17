"""The same part under two numbers: found as it is asked for, and listed when
the library already has it."""

import pytest

from app.domain.duplicates import Candidate, alike, duplicates, same_value
from app.domain.parts import MpnRef, Part
from app.library.parts import load_parts
from tests.conftest import OPEN, sign_in
from tests.support import PART


def part(ipn, *, footprint="Passives:R_0603", status="approved", mpns=(), **fields):
    return Part(
        ipn=ipn,
        description=f"Resistor {fields.get('Value', '')}".strip(),
        status=status,
        symbol="Passives:R",
        footprint=footprint,
        fields=fields,
        mpns=tuple(MpnRef(mpn, preferred=i == 0) for i, mpn in enumerate(mpns)),
    )


@pytest.mark.parametrize(
    "a,b,same",
    [
        ("4k7", "4.7k", True),
        ("4.7 kΩ", "4k7", True),
        ("100mW", "0.1W", True),
        ("1%", "1 %", True),
        ("X7R", "x7r", True),
        ("1%", "5%", False),
        ("100nF 50V X7R", "100nF 16V X7R", False),
        ("SOT-23", "SOT-23-6", False),
    ],
)
def test_a_value_is_the_same_however_it_is_written(a, b, same):
    assert same_value(a, b) is same


# -- as a part is asked for ---------------------------------------------------

LIBRARY = [
    part("1102-0001", Value="10k", Tolerance="1%", mpns=["RC0603FR-0710KL"]),
    part("1102-0002", Value="4k7", Tolerance="1%"),
    part("1102-0003", Value="10k", Tolerance="5%"),
    part("1102-0004", Value="10k", Tolerance="1%", footprint="Passives:R_0805"),
    part("1101-0001", Value="10k", footprint="Passives:R_0603"),
]


def found(candidate):
    return {each.part.ipn: each.reasons for each in alike(candidate, LIBRARY)}


def test_a_part_with_the_same_fields_and_footprint_is_found():
    seen = found(Candidate(category="1102", footprint="Passives:R_0603", fields={"Value": "10.0k"}))

    # 10k in 0603 twice: whatever their tolerance, the value is all that was said.
    assert set(seen) == {"1102-0001", "1102-0003"}
    assert seen["1102-0001"] == ("same footprint Passives:R_0603 and the same Value",)


def test_a_field_that_differs_rules_a_part_out():
    seen = found(
        Candidate(
            category="1102",
            footprint="Passives:R_0603",
            fields={"Value": "10k", "Tolerance": "1%"},
        )
    )

    assert set(seen) == {"1102-0001"}, "not the 5% one, not the 0805 one, not a capacitor"


def test_a_shared_manufacturer_part_is_found_in_any_category_and_comes_first():
    seen = alike(
        Candidate(
            category="1101",
            footprint="Passives:R_0603",
            fields={"Value": "10k"},
            mpns=["rc0603fr-0710kl"],
        ),
        LIBRARY,
    )

    assert seen[0].part.ipn == "1102-0001"
    assert seen[0].reasons == ("also lists rc0603fr-0710kl",)
    assert [each.part.ipn for each in seen] == ["1102-0001", "1101-0001"]


def test_the_part_being_edited_is_not_like_itself():
    seen = found(
        Candidate(
            ipn="1102-0001",
            category="1102",
            footprint="Passives:R_0603",
            fields={"Value": "10k", "Tolerance": "1%"},
            mpns=["RC0603FR-0710KL"],
        )
    )

    assert seen == {}


def test_nothing_said_finds_nothing():
    assert found(Candidate(category="1102")) == {}


# -- in the library -----------------------------------------------------------


def test_the_same_part_twice_is_reported_against_the_later_number():
    reported = duplicates(
        [
            part("1102-0007", Value="10k", Tolerance="1%"),
            part("1102-0001", Value="10K", Tolerance="1 %"),
            part("1102-0002", Value="10k"),  # says less: not the same here
            part("1102-0003", Value="10k", Tolerance="1%", status="obsolete"),
        ]
    )

    assert [(d.part.ipn, d.earlier.ipn, d.reason) for d in reported] == [
        ("1102-0007", "1102-0001", "same footprint and the same fields")
    ]


def test_two_parts_preferring_one_manufacturer_part_are_reported():
    reported = duplicates(
        [
            part("1102-0001", Value="10k", mpns=["RC0603FR-0710KL"]),
            part("1102-0002", Value="10k 1%", mpns=["RC0603FR-0710KL"]),
            part("1101-0001", Value="10k", mpns=["RC0603FR-0710KL"]),  # another category
        ]
    )

    assert [(d.part.ipn, d.reason) for d in reported] == [
        ("1102-0002", "both prefer RC0603FR-0710KL")
    ]


def test_a_duplicate_is_on_the_issues_page(tree):
    tree.standard()
    tree.part("1102-0002", PART.replace("RC0603FR-0710KL", "ERJ-3EKF1002V"))

    issues = [issue for issue in load_parts(tree.root).issues if issue.kind == "duplicate-part"]

    assert [(issue.message, issue.path) for issue in issues] == [
        (
            "1102-0002 looks the same as 1102-0001: same footprint and the same fields",
            "parts/1102/1102-0002.yaml",
        )
    ]


# -- over the API ----------------------------------------------------------


def test_the_form_asks_what_is_alike(make_client, tree):
    client = sign_in(make_client(tree.standard(), **OPEN))

    answer = client.post(
        "/api/v1/parts/alike",
        json={
            "category": "1102",
            "footprint": "Passives:R_0603_1608Metric",
            "fields": {"Value": "10.0k"},
            "mpns": [],
        },
    ).json()

    assert answer == {
        "parts": [
            {
                "ipn": "1102-0001",
                "description": "Resistor 10k 1% 0.1W 0603",
                "status": "approved",
                "reasons": ["same footprint Passives:R_0603_1608Metric and the same Value"],
            }
        ]
    }


def test_the_form_is_told_which_parts_list_a_manufacturer_part(make_client, tree):
    client = sign_in(make_client(tree.standard(), **OPEN))

    mpns = client.get("/api/v1/library/choices").json()["mpns"]

    assert mpns == [
        {
            "mpn": "RC0603FR-0710KL",
            "manufacturer": "Yageo",
            "lifecycle": "active",
            "used_by": ["1102-0001"],
        }
    ]
