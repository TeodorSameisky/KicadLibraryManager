"""DigiKey, through its Product Information API (v4).

A part is looked up by keyword search on its manufacturer's part number:
`POST /products/v4/search/keyword`. That, and not the product-details call,
because a manufacturer's number is not DigiKey's own: the search returns
every product that goes by it, and the one meant is picked out by who makes
it. Each product comes with its stock, its status and its price breaks, so
one call says everything shown here.

Access is the two-legged OAuth flow: the client id and secret are exchanged
for a token that lasts ten minutes, which is kept and asked for again when it
runs out. Getting a token is not a call against the day's limit; a search is.

The sandbox (`https://sandbox-api.digikey.com`) answers the same way with
made-up data, for trying the keys out.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

import httpx

from app.domain.manufacturers import Manufacturers, name_key
from app.domain.parts import Lifecycle, is_web_address
from app.domain.supply import Offer, PriceBreak, Query
from app.suppliers.base import Fetched, SupplierError

log = logging.getLogger(__name__)

PRODUCTION = "https://api.digikey.com"

# A token is asked for again this long before it runs out, so that one about
# to expire is not sent on a call that then arrives too late.
_TOKEN_MARGIN_SECONDS = 30

# How many products to ask for. The one meant is among the first few or it
# is not there: these are the closest matches to an exact number.
_LIMIT = 10

# How much of DigiKey's reason for not answering is quoted.
_SAID_LENGTH = 300


def _number(text: object) -> str:
    """A part number as it is compared: without case or the spaces around it."""
    return str(text or "").strip().upper()


def _whole(value: object) -> int | None:
    """A count, from a number or from text that is one. DigiKey sends both."""
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _address(value: object) -> str | None:
    """A link out of an answer, if it is one a page can safely follow."""
    text = str(value or "").strip()
    if text.startswith("//"):  # DigiKey's own media, written without the scheme
        text = f"https:{text}"
    return text if is_web_address(text) else None


def lifecycle_of(status: str, *, discontinued: bool = False, end_of_life: bool = False) -> str:
    """What DigiKey's status means in the library's words.

    "Discontinued at DigiKey" is the distributor's decision and says nothing
    about the manufacturer, so it is not taken for obsolete -- but a part the
    manufacturer has announced the end of is not one for new designs,
    whatever the status line still says.
    """
    said = status.casefold()
    if "obsolete" in said:
        return Lifecycle.OBSOLETE.value
    if "not for new designs" in said or "last time buy" in said or end_of_life:
        return Lifecycle.NRND.value
    if said == "active" and not discontinued:
        return Lifecycle.ACTIVE.value
    return Lifecycle.UNKNOWN.value


class DigiKey:
    id = "digikey"
    name = "DigiKey"

    def __init__(
        self,
        client_id: str | None,
        client_secret: str | None,
        *,
        base_url: str = PRODUCTION,
        site: str = "US",
        language: str = "en",
        currency: str = "USD",
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client_id = client_id or ""
        self._client_secret = client_secret or ""
        self._base_url = base_url.rstrip("/")
        self._site, self._language, self._currency = site, language, currency
        self._timeout = timeout_seconds
        self._transport = transport
        self._clock = clock
        self._token: str | None = None
        self._token_expires = 0.0
        self._token_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def asks(self, query: Query) -> str:
        # The country and currency are in it: the same part has another price
        # and another stock for another country.
        return (
            f"keyword {_number(query.mpn)} "
            f"site={self._site} language={self._language} currency={self._currency}"
        )

    # -- asking

    def _http(self) -> httpx.Client:
        return httpx.Client(timeout=self._timeout, transport=self._transport)

    def _fresh_token(self, http: httpx.Client) -> str:
        with self._token_lock:
            if self._token and self._clock() < self._token_expires - _TOKEN_MARGIN_SECONDS:
                return self._token

            answer = http.post(
                f"{self._base_url}/v1/oauth2/token",
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                },
            )
            try:
                granted = answer.json() if answer.status_code == 200 else {}
            except ValueError:
                granted = {}
            token = granted.get("access_token") if isinstance(granted, dict) else None
            if not token:
                # The status and DigiKey's own reason, never what was sent:
                # that holds the secret.
                said = self._said(answer.text)
                raise SupplierError(
                    f"DigiKey gave no access token (HTTP {answer.status_code}"
                    f"{f': {said}' if said else ''}). "
                    "Check DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET, that they are "
                    f"the keys for {self._base_url} (a sandbox application has its own), "
                    "and that the application is subscribed to Product Information v4"
                )
            self._token = str(token)
            self._token_expires = self._clock() + (_whole(granted.get("expires_in")) or 600)
            return self._token

    def _forget_token(self) -> None:
        with self._token_lock:
            self._token = None

    def _said(self, body: str) -> str:
        """DigiKey's reason for not answering, in a line.

        Its errors come in two shapes: `ErrorMessage` and `ErrorDetails` from
        the token call, `title` and `detail` from the others. Anything else is
        quoted as it came, cut short. The keys are taken out of it, should
        DigiKey ever say them back.
        """
        try:
            data = json.loads(body)
        except ValueError:
            data = None
        if isinstance(data, dict):
            parts = [
                str(data[key]).strip()
                for key in ("ErrorMessage", "ErrorDetails", "title", "detail")
                if isinstance(data.get(key), str) and data[key].strip()
            ]
            text = ": ".join(parts)
        else:
            text = body
        text = " ".join(text.split())
        for key in (self._client_secret, self._client_id):
            if key:
                text = text.replace(key, "…")
        return text if len(text) <= _SAID_LENGTH else f"{text[:_SAID_LENGTH]}…"

    def _search(self, http: httpx.Client, query: Query) -> httpx.Response:
        return http.post(
            f"{self._base_url}/products/v4/search/keyword",
            json={"Keywords": query.mpn.strip(), "Limit": _LIMIT, "Offset": 0},
            headers={
                "Authorization": f"Bearer {self._fresh_token(http)}",
                "X-DIGIKEY-Client-Id": self._client_id,
                "X-DIGIKEY-Locale-Site": self._site,
                "X-DIGIKEY-Locale-Language": self._language,
                "X-DIGIKEY-Locale-Currency": self._currency,
                "X-DIGIKEY-Customer-Id": "0",
                "Accept": "application/json",
            },
        )

    def fetch(self, query: Query) -> Fetched:
        if not self.configured:
            raise SupplierError("DigiKey is not set up: there is no client id and secret")
        try:
            with self._http() as http:
                answer = self._search(http, query)
                if answer.status_code == 401:
                    # A token can be withdrawn before its time. Once more with
                    # a new one; a second refusal is about the keys.
                    self._forget_token()
                    answer = self._search(http, query)
        except httpx.HTTPError as exc:
            raise SupplierError(f"DigiKey could not be reached: {exc}") from exc

        fetched = Fetched(
            status=answer.status_code,
            body=answer.text,
            remaining=_whole(answer.headers.get("X-RateLimit-Remaining")),
        )
        if fetched.answered:
            return fetched
        return replace(fetched, said=self._said(answer.text))

    # -- reading

    def read(self, body: str, query: Query, manufacturers: Manufacturers) -> Offer | None:
        try:
            data = json.loads(body)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None

        product = self._meant(data, query, manufacturers)
        if product is None:
            return None

        maker = product.get("Manufacturer")
        described = product.get("Description")
        state = product.get("ProductStatus")
        status = str(state.get("Status") or "") if isinstance(state, dict) else str(state or "")
        variation = self._to_buy(product)
        package = variation.get("PackageType")
        locale = data.get("SearchLocaleUsed") or (data.get("SettingsUsed") or {}).get(
            "SearchLocaleUsed"
        )

        return Offer(
            supplier=self.id,
            mpn=str(product.get("ManufacturerProductNumber") or query.mpn),
            manufacturer=str(maker.get("Name") or "") if isinstance(maker, dict) else "",
            sku=str(variation.get("DigiKeyProductNumber") or ""),
            description=(
                str(described.get("ProductDescription") or "")
                if isinstance(described, dict)
                else ""
            ),
            stock=_whole(product.get("QuantityAvailable")),
            lifecycle=lifecycle_of(
                status,
                discontinued=bool(product.get("Discontinued")),
                end_of_life=bool(product.get("EndOfLife")),
            ),
            status=status,
            prices=self._prices(variation),
            currency=(str(locale.get("Currency") or "") if isinstance(locale, dict) else "")
            or self._currency,
            packaging=str(package.get("Name") or "") if isinstance(package, dict) else "",
            minimum=_whole(variation.get("MinimumOrderQuantity")),
            lead_weeks=_whole(product.get("ManufacturerLeadWeeks")),
            url=_address(product.get("ProductUrl")),
            datasheet=_address(product.get("DatasheetUrl")),
        )

    @staticmethod
    def _meant(
        data: Mapping[str, Any], query: Query, manufacturers: Manufacturers
    ) -> Mapping[str, Any] | None:
        """The product the query is about, among what the search found.

        It has to go by exactly the number asked for: a search also returns
        what is merely like it. Where several do -- one number, two makers --
        the one from the manufacturer asked for is meant. Where only one
        does, it is taken even if DigiKey calls its maker something the
        library does not recognise: a number is very nearly a name.
        """
        found: list[Mapping[str, Any]] = []
        for group in ("ExactMatches", "Products"):
            listed = data.get(group)
            for product in listed if isinstance(listed, list) else []:
                same_number = isinstance(product, dict) and _number(
                    product.get("ManufacturerProductNumber")
                ) == _number(query.mpn)
                if same_number and product not in found:
                    found.append(product)
        if not found:
            return None

        wanted = name_key(manufacturers.canonical(query.manufacturer))
        if wanted:
            for product in found:
                maker = product.get("Manufacturer")
                theirs = str(maker.get("Name") or "") if isinstance(maker, dict) else ""
                if name_key(manufacturers.canonical(theirs)) == wanted:
                    return product
        return found[0] if len(found) == 1 else None

    @staticmethod
    def _to_buy(product: Mapping[str, Any]) -> Mapping[str, Any]:
        """The packaging someone buying a few would order: DigiKey's own
        stock, in the form with the smallest order."""
        listed = product.get("ProductVariations")
        variations = [
            v for v in (listed if isinstance(listed, list) else []) if isinstance(v, dict)
        ]
        priced = [v for v in variations if v.get("StandardPricing") and not v.get("MarketPlace")]
        candidates = priced or [v for v in variations if v.get("StandardPricing")] or variations
        if not candidates:
            return {}
        return min(candidates, key=lambda v: _whole(v.get("MinimumOrderQuantity")) or 1)

    @staticmethod
    def _prices(variation: Mapping[str, Any]) -> tuple[PriceBreak, ...]:
        listed = variation.get("StandardPricing")
        steps = []
        for step in listed if isinstance(listed, list) else []:
            quantity = _whole(step.get("BreakQuantity")) if isinstance(step, dict) else None
            try:
                price = float(step["UnitPrice"])
            except (KeyError, TypeError, ValueError):
                continue
            if quantity is not None and quantity > 0:
                steps.append(PriceBreak(quantity, price))
        return tuple(sorted(steps, key=lambda step: step.quantity))
