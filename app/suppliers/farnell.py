"""Farnell -- and Newark and element14, which are one catalogue -- through the
Product Search API.

A part is looked up by its manufacturer's part number:
`GET /catalog/products?term=manuPartNum:<number>`. Which country's catalogue
is asked is the *store*: `de.farnell.com`, `uk.farnell.com`, `www.newark.com`.
The store decides the stock, the prices, and the currency the prices are in --
which the answer does not say, so it is worked out from the store here, or
told to this class.

One number is often several products: on a reel, as cut tape, re-reeled.
The one shown is the one someone buying a few would order: priced, with the
smallest order.

The key travels in the address of the request, as with Mouser: nothing that
might quote an address is passed on with the key in it. A call the gateway in
front of the API turns away -- a key that is not active, too many calls a
second or a day -- comes back as HTTP 403 with the reason in a header.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from app.domain.manufacturers import Manufacturers
from app.domain.parts import Lifecycle
from app.domain.supply import Offer, PriceBreak, Query
from app.suppliers.base import Fetched, SupplierError, TooManyCalls
from app.suppliers.reading import address, matching, number, weeks, whole

PRODUCTION = "https://api.element14.com"

# How many products to ask for: the forms one number is sold in.
_LIMIT = 10

# How much of Farnell's reason for not answering is quoted.
_SAID_LENGTH = 300

# What each store's prices are in, where it is not the euro. The answer does
# not say, and a price without a currency is not a price.
_CURRENCIES = {
    "uk.farnell.com": "GBP",
    "cpc.farnell.com": "GBP",
    "onecall.farnell.com": "GBP",
    "ch.farnell.com": "CHF",
    "dk.farnell.com": "DKK",
    "no.farnell.com": "NOK",
    "se.farnell.com": "SEK",
    "pl.farnell.com": "PLN",
    "cz.farnell.com": "CZK",
    "hu.farnell.com": "HUF",
    "www.newark.com": "USD",
    "canada.newark.com": "CAD",
    "au.element14.com": "AUD",
    "nz.element14.com": "NZD",
    "sg.element14.com": "SGD",
    "hk.element14.com": "HKD",
    "in.element14.com": "INR",
    "cn.element14.com": "CNY",
}


def currency_of(store: str) -> str:
    """What a store's prices are in, as far as is known here: the euro for
    the Farnell stores not listed otherwise, and nothing for a store this
    does not know, which is better than a guess."""
    store = store.strip().lower()
    if store in _CURRENCIES:
        return _CURRENCIES[store]
    return "EUR" if store.endswith(".farnell.com") else ""


# productStatus, in words for a page. Anything else is shown as it came.
_STATUS = {
    "STOCKED": "Stocked",
    "DIRECT_SHIP": "Shipped direct",
    "NO_LONGER_STOCKED": "No longer stocked",
    "NO_LONGER_MANUFACTURED": "No longer manufactured",
}


def lifecycle_of(status: str, *, release: int | None = None, stock: int | None = None) -> str:
    """What Farnell says about a product's standing, in the library's words.

    Three fields say it, and none of them alone: `productStatus`, the
    `releaseStatusCode` (4 active, 6 to be discontinued, 7 discontinued) and
    the stock's own status (-3 no longer manufactured). That Farnell no
    longer stocks a part is Farnell's decision and says nothing about the
    manufacturer; only "no longer manufactured" is taken for obsolete.
    """
    if status == "NO_LONGER_MANUFACTURED" or stock == -3:
        return Lifecycle.OBSOLETE.value
    if release == 6:
        return Lifecycle.NRND.value
    if status == "NO_LONGER_STOCKED" or release == 7:
        return Lifecycle.UNKNOWN.value
    if status in ("STOCKED", "DIRECT_SHIP") or release in (1, 2, 4):
        return Lifecycle.ACTIVE.value
    return Lifecycle.UNKNOWN.value


class Farnell:
    id = "farnell"
    name = "Farnell"
    keys = ("FARNELL_API_KEY",)

    def __init__(
        self,
        api_key: str | None,
        *,
        store: str = "uk.farnell.com",
        currency: str | None = None,
        base_url: str = PRODUCTION,
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._key = api_key or ""
        self._store = store.strip().lower()
        self._currency = (currency or "").strip().upper() or currency_of(self._store)
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self._key)

    def asks(self, query: Query) -> str:
        # The store is in it: the same part has another price and another
        # stock in another country's catalogue.
        return f"manuPartNum {number(query.mpn)} store={self._store}"

    # -- asking

    def _clean(self, text: str) -> str:
        """Text that may quote what was sent, with the key taken out and any
        markup with it, in one line and cut short."""
        text = " ".join(re.sub(r"<[^>]*>", " ", text).split())
        if self._key:
            text = text.replace(self._key, "…")
        return text if len(text) <= _SAID_LENGTH else f"{text[:_SAID_LENGTH]}…"

    def _said(self, answer: httpx.Response) -> str:
        """Why the call was turned away: the gateway's header, the API's own
        fault, or failing both what the body says."""
        code = answer.headers.get("X-Mashery-Error-Code", "")
        detail = answer.headers.get("X-Error-Detail-Header", "")
        if code or detail:
            return self._clean(f"{detail} ({code})" if detail and code else detail or code)
        try:
            data = json.loads(answer.text)
        except ValueError:
            return self._clean(answer.text)
        found = re.findall(r'"(?:exceptionString|faultstring)"\s*:\s*"([^"]*)"', json.dumps(data))
        return self._clean(" ".join(dict.fromkeys(found)) or answer.text)

    def fetch(self, query: Query) -> Fetched:
        if not self.configured:
            raise SupplierError("Farnell is not set up: there is no API key")
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as http:
                answer = http.get(
                    f"{self._base_url}/catalog/products",
                    params={
                        "term": f"manuPartNum:{query.mpn.strip()}",
                        "storeInfo.id": self._store,
                        "resultsSettings.offset": "0",
                        "resultsSettings.numberOfResults": str(_LIMIT),
                        "resultsSettings.responseGroup": "large",
                        "callInfo.responseDataFormat": "JSON",
                        "callInfo.apiKey": self._key,
                    },
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise SupplierError(f"Farnell could not be reached: {self._clean(str(exc))}") from exc

        fetched = Fetched(status=answer.status_code, body=answer.text)
        if fetched.answered:
            return fetched

        said = self._said(answer)
        code = answer.headers.get("X-Mashery-Error-Code", "").upper()
        if answer.status_code == 429 or "OVER_QPS" in code or "OVER_RATE" in code:
            raise TooManyCalls(
                f"Farnell says it has been called too often (HTTP {answer.status_code}: {said})",
                # So many a second: it will answer again at once. So many a
                # day is left to `app.services.supply` to wait out.
                retry_after=2.0 if "OVER_QPS" in code else None,
            )
        if answer.status_code == 403:
            raise SupplierError(
                f"Farnell turned the call away (HTTP 403: {said}). Check FARNELL_API_KEY: "
                "a key is not active until its registration has been confirmed"
            )
        return Fetched(status=answer.status_code, body=answer.text, said=said)

    # -- reading

    def read(self, body: str, query: Query, manufacturers: Manufacturers) -> Offer | None:
        try:
            data = json.loads(body)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        # Under a name that says which search it was: for this one,
        # "manufacturerPartNumberSearchReturn". Whichever holds the products.
        listed: object = data.get("products")
        for found in data.values():
            if isinstance(found, dict) and isinstance(found.get("products"), list):
                listed = found["products"]
        products = [p for p in (listed if isinstance(listed, list) else []) if isinstance(p, dict)]

        matched = matching(
            products,
            query,
            manufacturers,
            number_of=lambda product: product.get("translatedManufacturerPartNumber"),
            maker_of=lambda product: product.get("brandName") or product.get("vendorName"),
        )
        if not matched:
            return None
        priced = [product for product in matched if product.get("prices")] or matched
        product = min(
            priced, key=lambda product: whole(product.get("translatedMinimumOrderQuality")) or 1
        )

        held = product.get("stock")
        stock: Mapping[str, Any] = held if isinstance(held, dict) else {}
        status = str(product.get("productStatus") or "").strip()
        sku = str(product.get("sku") or "").strip()
        sheets = product.get("datasheets")
        sheet = sheets[0] if isinstance(sheets, list) and sheets else None
        level = whole(stock.get("level"))

        return Offer(
            supplier=self.id,
            mpn=str(product.get("translatedManufacturerPartNumber") or query.mpn),
            manufacturer=str(product.get("brandName") or product.get("vendorName") or ""),
            sku=sku,
            description=self._description(product),
            stock=level if level is not None else whole(product.get("inv")),
            lifecycle=lifecycle_of(
                status,
                release=whole(product.get("releaseStatusCode")),
                stock=whole(stock.get("status")),
            ),
            # "defaultStatus" is Farnell saying nothing in particular.
            status=_STATUS.get(status, "" if status.casefold() == "defaultstatus" else status),
            prices=self._prices(product),
            currency=self._currency,
            packaging=str(product.get("packageName") or "").strip(),
            minimum=whole(product.get("translatedMinimumOrderQuality")),
            lead_weeks=weeks(stock.get("leastLeadTime")),
            # Its own page if the answer gives one; otherwise the store's
            # search for the order code, which leads there.
            url=address(product.get("productURL"))
            or (f"https://{self._store}/search?st={quote(sku)}" if sku else None),
            datasheet=address(sheet.get("url")) if isinstance(sheet, dict) else None,
        )

    @staticmethod
    def _description(product: Mapping[str, Any]) -> str:
        """ "SCHURTER - 0001.1001 - FUSE, FAST ACTING, CERAMIC, 500MA": what
        comes after the maker and the number, which are said elsewhere."""
        name = str(product.get("displayName") or "").strip()
        pieces = name.split(" - ", 2)
        return pieces[2].strip() if len(pieces) == 3 else name

    @staticmethod
    def _prices(product: Mapping[str, Any]) -> tuple[PriceBreak, ...]:
        listed = product.get("prices")
        steps = []
        for step in listed if isinstance(listed, list) else []:
            quantity = whole(step.get("from")) if isinstance(step, dict) else None
            try:
                price = float(step["cost"])
            except (KeyError, TypeError, ValueError):
                continue
            if quantity is not None and quantity > 0:
                steps.append(PriceBreak(quantity, price))
        return tuple(sorted(steps, key=lambda step: step.quantity))
