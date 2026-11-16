"""What reading any supplier's answer comes to.

Every supplier answers a search for a manufacturer's part number with a list
of products, in its own words and its own shapes. Picking the one that was
meant out of that list, and turning what it says into counts, prices and
links, is the same job each time.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable

from app.domain.manufacturers import Manufacturers, name_key
from app.domain.parts import is_web_address
from app.domain.supply import Query


def number(text: object) -> str:
    """A part number as it is compared: without case or the spaces around it."""
    return str(text or "").strip().upper()


def whole(value: object) -> int | None:
    """A count, from a number or from text that is one. Suppliers send both."""
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def address(value: object) -> str | None:
    """A link out of an answer, if it is one a page can safely follow."""
    text = str(value or "").strip()
    if text.startswith("//"):  # written without the scheme
        text = f"https:{text}"
    return text if is_web_address(text) else None


def weeks(days: object) -> int | None:
    """A lead time given in days, as weeks; None for none, or none worth
    saying."""
    count = whole(days)
    return math.ceil(count / 7) if count is not None and count > 0 else None


# Digits with what countries put between them: points, commas, spaces,
# apostrophes.
_DIGITS = re.compile(r"\d[\d.,\s']*\d|\d")


def amount(text: object, *, comma_is_decimal: bool) -> float | None:
    """A price written for people -- "$1,234.56", "0,089 €" -- as a number.

    Which of the comma and the point is the decimal mark cannot be told from
    one price alone: "1,234" is a little over one in Germany and over a
    thousand in the United States. Where both are there, the last is the
    decimal mark. A lone point is one. A lone comma is one where
    `comma_is_decimal` says the price's country writes numbers that way.
    """
    found = _DIGITS.search(str(text or ""))
    if found is None:
        return None
    digits = re.sub(r"[\s']", "", found.group())
    comma, point = digits.rfind(","), digits.rfind(".")
    decimal = ""
    if comma >= 0 and point >= 0:
        decimal = "," if comma > point else "."
    elif point >= 0 and digits.count(".") == 1:
        decimal = "."
    elif comma >= 0 and digits.count(",") == 1 and comma_is_decimal:
        decimal = ","
    plain = "".join(
        "." if character == decimal else character
        for character in digits
        if character.isdigit() or character == decimal
    )
    try:
        return float(plain)
    except ValueError:
        return None


def matching[P](
    products: Iterable[P],
    query: Query,
    manufacturers: Manufacturers,
    *,
    number_of: Callable[[P], object],
    maker_of: Callable[[P], object],
) -> list[P]:
    """The products a query is about, among what a search found: usually
    one, several where one part is sold in several forms, none if it is not
    there or cannot be told.

    A product has to go by exactly the number asked for: a search also
    returns what is merely like it. Where two manufacturers make something
    under one number, those from the manufacturer asked for are meant. Where
    everything by that number is from one manufacturer it is taken, even if
    the supplier calls that manufacturer something the library does not
    recognise: a number is very nearly a name.
    """
    found: list[P] = []
    for product in products:
        if number(number_of(product)) == number(query.mpn) and product not in found:
            found.append(product)
    if not found:
        return []

    def maker(product: P) -> str:
        return name_key(manufacturers.canonical(str(maker_of(product) or "")))

    wanted = name_key(manufacturers.canonical(query.manufacturer))
    theirs = [product for product in found if maker(product) == wanted] if wanted else []
    if theirs:
        return theirs
    return found if len({maker(product) for product in found}) == 1 else []


def meant[P](
    products: Iterable[P],
    query: Query,
    manufacturers: Manufacturers,
    *,
    number_of: Callable[[P], object],
    maker_of: Callable[[P], object],
) -> P | None:
    """The product a query is about: the first of `matching`, or None."""
    found = matching(products, query, manufacturers, number_of=number_of, maker_of=maker_of)
    return found[0] if found else None
