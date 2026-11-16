"""Mouser, through its Search API (v1).

A part is looked up by the part number search:
`POST /api/v1/search/partnumber`, with the manufacturer's part number. It
answers with every part whose number begins that way, so the one meant is
picked out of the list: the one that goes by exactly the number, from the
part's manufacturer.

Two things about Mouser that are not like the others:

The key travels in the address of the request (`?apiKey=`), because that is
where Mouser wants it. So nothing that might quote an address -- an error, a
log line -- is passed on without the key taken out of it, and httpx's own
logging of requests is turned down in `app.observability`.

And it says no politely: a search it refuses -- a key it does not know, too
many calls -- comes back as HTTP 200 with the reason in `Errors`. Read as an
answer, that would be kept for a day as "Mouser does not list it". So the
reason is looked for here, and a refusal is raised and not returned.

Whose prices and stock a search returns is decided by the key: Mouser ties
it to the country and currency of the account it was made for.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from app.domain.manufacturers import Manufacturers
from app.domain.parts import Lifecycle
from app.domain.supply import Offer, PriceBreak, Query
from app.suppliers.base import Fetched, SupplierError, TooManyCalls
from app.suppliers.reading import address, amount, matching, number, weeks, whole

PRODUCTION = "https://api.mouser.com"

# How much of Mouser's reason for not answering is quoted.
_SAID_LENGTH = 300

# The currencies whose prices are written with a point for the decimals and a
# comma between the thousands. Everywhere else a lone comma is the decimals.
_POINT_COUNTRIES = frozenset(
    {"USD", "GBP", "CAD", "AUD", "NZD", "SGD", "HKD", "JPY", "CNY", "INR", "MXN", "ILS", "CHF"}
    | {"THB", "MYR", "PHP", "KRW", "TWD"}
)


def lifecycle_of(status: str, *, discontinued: bool = False) -> str:
    """What Mouser's lifecycle status means in the library's words.

    Mouser says something only where there is something to say: a part in
    ordinary production has no status at all. "Discontinued" is Mouser no
    longer selling it, which says nothing certain about the manufacturer.
    """
    said = status.casefold().strip()
    if "obsolete" in said:
        return Lifecycle.OBSOLETE.value
    if any(word in said for word in ("end of life", "not recommended", "nrnd", "last time buy")):
        return Lifecycle.NRND.value
    if discontinued:
        return Lifecycle.UNKNOWN.value
    if said in ("", "new product", "new at mouser", "active"):
        return Lifecycle.ACTIVE.value
    return Lifecycle.UNKNOWN.value


class Mouser:
    id = "mouser"
    name = "Mouser"
    keys = ("MOUSER_API_KEY",)

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = PRODUCTION,
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._key = api_key or ""
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self._key)

    def asks(self, query: Query) -> str:
        return f"partnumber {number(query.mpn)}"

    # -- asking

    def _clean(self, text: str) -> str:
        """Text that may quote what was sent, with the key taken out, in one
        line and cut short."""
        text = " ".join(text.split())
        if self._key:
            text = text.replace(self._key, "…")
        return text if len(text) <= _SAID_LENGTH else f"{text[:_SAID_LENGTH]}…"

    def _errors(self, body: str) -> str:
        """Why Mouser would not answer, if it says so: its `Errors`, in a
        line. Empty for an answer that is one."""
        try:
            data = json.loads(body)
        except ValueError:
            return ""
        listed = data.get("Errors") if isinstance(data, dict) else None
        said = []
        for error in listed if isinstance(listed, list) else []:
            if not isinstance(error, dict):
                continue
            message = str(error.get("Message") or error.get("Code") or "").strip()
            about = str(error.get("PropertyName") or "").strip()
            if message:
                said.append(f"{message} ({about})" if about else message)
        return self._clean(" ".join(said))

    def fetch(self, query: Query) -> Fetched:
        if not self.configured:
            raise SupplierError("Mouser is not set up: there is no API key")
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as http:
                answer = http.post(
                    f"{self._base_url}/api/v1/search/partnumber",
                    params={"apiKey": self._key},
                    # Not "Exact": that is documented for Mouser's own numbers
                    # only. What begins with the number comes back, and the
                    # one that goes by exactly it is picked out in `read`.
                    json={
                        "SearchByPartRequest": {
                            "mouserPartNumber": query.mpn.strip(),
                            "partSearchOptions": "None",
                        }
                    },
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise SupplierError(f"Mouser could not be reached: {self._clean(str(exc))}") from exc

        refused = self._errors(answer.text)
        too_many = answer.status_code == 429 or any(
            sign in refused.casefold() for sign in ("too many", "toomany", "maximum calls")
        )
        if too_many:
            raise TooManyCalls(
                f"Mouser says it has been called too often (HTTP {answer.status_code}"
                f"{f': {refused}' if refused else ''})"
            )
        if refused:
            hint = (
                " Check MOUSER_API_KEY: it has to be a key for the Search API."
                if "key" in refused.casefold() or "identifier" in refused.casefold()
                else ""
            )
            raise SupplierError(f"Mouser refused the search: {refused}.{hint}")

        fetched = Fetched(status=answer.status_code, body=answer.text)
        if fetched.answered:
            return fetched
        return Fetched(status=answer.status_code, body=answer.text, said=self._clean(answer.text))

    # -- reading

    def read(self, body: str, query: Query, manufacturers: Manufacturers) -> Offer | None:
        try:
            data = json.loads(body)
        except ValueError:
            return None
        results = data.get("SearchResults") if isinstance(data, dict) else None
        listed = results.get("Parts") if isinstance(results, dict) else None
        parts = [
            part for part in (listed if isinstance(listed, list) else []) if isinstance(part, dict)
        ]

        found = matching(
            parts,
            query,
            manufacturers,
            number_of=lambda part: part.get("ManufacturerPartNumber"),
            maker_of=lambda part: part.get("Manufacturer"),
        )
        if not found:
            return None
        # One number can be sold in more than one form. The one someone buying
        # a few would order: priced, with the smallest order.
        priced = [part for part in found if part.get("PriceBreaks")] or found
        part = min(priced, key=lambda part: whole(part.get("Min")) or 1)

        status = str(part.get("LifecycleStatus") or "").strip()
        prices, currency = self._prices(part)
        return Offer(
            supplier=self.id,
            mpn=str(part.get("ManufacturerPartNumber") or query.mpn),
            manufacturer=str(part.get("Manufacturer") or ""),
            sku=str(part.get("MouserPartNumber") or ""),
            description=str(part.get("Description") or ""),
            stock=self._stock(part),
            lifecycle=lifecycle_of(
                status, discontinued=str(part.get("IsDiscontinued") or "").casefold() == "true"
            ),
            status=status,
            prices=prices,
            currency=currency,
            packaging=self._packaging(part),
            minimum=whole(part.get("Min")),
            lead_weeks=self._lead_weeks(part.get("LeadTime")),
            url=address(part.get("ProductDetailUrl")),
            datasheet=address(part.get("DataSheetUrl")),
        )

    @staticmethod
    def _stock(part: Mapping[str, Any]) -> int | None:
        """How many are in stock. Mouser says it as a number in one field and
        as a sentence in another -- "6609 In Stock" -- and sometimes only the
        sentence."""
        counted = whole(part.get("AvailabilityInStock"))
        if counted is not None:
            return counted
        said = str(part.get("Availability") or "").strip()
        if "stock" in said.casefold():
            return whole(said.split()[0]) if said.split() else None
        return 0 if said.casefold() in ("none", "") and "Availability" in part else None

    @staticmethod
    def _lead_weeks(said: object) -> int | None:
        """ "56 Days", as weeks."""
        words = str(said or "").split()
        if not words:
            return None
        count = whole(words[0])
        if count is None or count <= 0:
            return None
        return count if len(words) > 1 and words[1].casefold().startswith("week") else weeks(count)

    @staticmethod
    def _packaging(part: Mapping[str, Any]) -> str:
        listed = part.get("ProductAttributes")
        forms = [
            str(attribute.get("AttributeValue") or "").strip()
            for attribute in (listed if isinstance(listed, list) else [])
            if isinstance(attribute, dict) and attribute.get("AttributeName") == "Packaging"
        ]
        return ", ".join(dict.fromkeys(form for form in forms if form))

    @staticmethod
    def _prices(part: Mapping[str, Any]) -> tuple[tuple[PriceBreak, ...], str]:
        """The price breaks and the currency they are in.

        A price comes as a number and as text written for people -- "$0.10",
        "0,089 €". The number is taken where there is one; older answers have
        only the text.
        """
        listed = part.get("PriceBreaks")
        steps, currency = [], ""
        for step in listed if isinstance(listed, list) else []:
            if not isinstance(step, dict):
                continue
            currency = currency or str(step.get("Currency") or "").strip().upper()
            quantity = whole(step.get("Quantity"))
            raw = step.get("RawPrice")
            price = (
                float(raw)
                if isinstance(raw, int | float) and not isinstance(raw, bool) and raw > 0
                else amount(step.get("Price"), comma_is_decimal=currency not in _POINT_COUNTRIES)
            )
            if quantity is not None and quantity > 0 and price is not None:
                steps.append(PriceBreak(quantity, price))
        return tuple(sorted(steps, key=lambda step: step.quantity)), currency
