"""What the tests build their worlds from.

Library files written from fragments, a library laid out on disk, and an
identity provider that exists only as an HTTP transport.
"""

from __future__ import annotations

import base64
import hashlib
import textwrap
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from app.auth.oidc import OidcClient
from app.library.snapshot import Snapshot, build_snapshot
from app.library.sources import Checkout, Source

# -- library files ----------------------------------------------------------


def symbol_file(
    name: str, *, extends: str | None = None, footprint: str = "", units: int = 0
) -> str:
    """A .kicad_sym holding one symbol.

    `units` follows KiCad's convention: unit 0 carries the body every unit
    shares, and units from 1 up carry the pins. A derived symbol has none.
    """
    lines = [
        "(kicad_symbol_lib",
        "\t(version 20251024)",
        '\t(generator "kicad_symbol_editor")',
        f'\t(symbol "{name}"',
    ]
    if extends:
        lines.append(f'\t\t(extends "{extends}")')
    lines += [
        '\t\t(property "Reference" "R"\n\t\t\t(at 2.032 0 90)\n\t\t)',
        f'\t\t(property "Value" "{name}"\n\t\t\t(at 0 0 90)\n\t\t)',
        f'\t\t(property "Footprint" "{footprint}"\n\t\t\t(at -1.778 0 90)\n\t\t\t(hide yes)\n\t\t)',
    ]

    for unit in range(units):
        lines.append(f'\t\t(symbol "{name}_{unit}_1"')
        if unit == 0:
            lines += [
                "\t\t\t(rectangle",
                "\t\t\t\t(start -1.016 -2.54)",
                "\t\t\t\t(end 1.016 2.54)",
                "\t\t\t)",
            ]
        else:
            for y, angle, number in (("3.81", "270", "1"), ("-3.81", "90", "2")):
                lines += [
                    "\t\t\t(pin passive line",
                    f"\t\t\t\t(at 0 {y} {angle})",
                    "\t\t\t\t(length 1.27)",
                    f'\t\t\t\t(number "{number}")',
                    "\t\t\t)",
                ]
        lines.append("\t\t)")

    lines += ["\t)", ")", ""]
    return "\n".join(lines)


def footprint_file(name: str, *, model: str | None = None) -> str:
    """A .kicad_mod with two pads, a silkscreen line and a courtyard."""
    lines = [f'(footprint "{name}"', '\t(layer "F.Cu")']
    for number, x in (("1", "-0.825"), ("2", "0.825")):
        lines += [
            f'\t(pad "{number}" smd roundrect',
            f"\t\t(at {x} 0)",
            "\t\t(size 0.8 0.95)",
            '\t\t(layers "F.Cu" "F.Mask" "F.Paste")',
            "\t\t(roundrect_rratio 0.25)",
            "\t)",
        ]
    lines += [
        "\t(fp_line",
        "\t\t(start -0.2 -0.5)",
        "\t\t(end 0.2 -0.5)",
        "\t\t(stroke\n\t\t\t(width 0.12)\n\t\t\t(type solid)\n\t\t)",
        '\t\t(layer "F.SilkS")',
        "\t)",
        "\t(fp_rect",
        "\t\t(start -1.5 -0.7)",
        "\t\t(end 1.5 0.7)",
        "\t\t(stroke\n\t\t\t(width 0.05)\n\t\t\t(type solid)\n\t\t)",
        "\t\t(fill no)",
        '\t\t(layer "F.CrtYd")',
        "\t)",
    ]
    if model:
        lines += [f'\t(model "{model}"', "\t\t(offset\n\t\t\t(xyz 0 0 0)\n\t\t)", "\t)"]
    lines += [")", ""]
    return "\n".join(lines)


CATEGORIES = """\
- code: "1101"
  name: Capacitors, fixed
- code: "1102"
  name: Resistors, fixed
"""

MPN = """\
mpn: RC0603FR-0710KL
manufacturer: Yageo
description: Thick film resistor
datasheet: https://example.com/yageo.pdf
lifecycle: active
"""

PART = """\
ipn: 1102-0001
description: Resistor 10k 1% 0.1W 0603
status: approved
symbol: Passives:R_0603
footprint: Passives:R_0603_1608Metric
fields:
  Value: 10k
  Tolerance: 1%
mpns:
  - mpn: RC0603FR-0710KL
    preferred: true
"""

MODEL_REF = "${KICAD_LIBRARY_3DMODELS}/Passives.3dshapes/R_0603_1608Metric.step"
MODEL_PATH = "3dmodels/Passives.3dshapes/R_0603_1608Metric.step"
STEP = "ISO-10303-21;\nHEADER;\nENDSEC;\n" * 20

SOURCE = Source(id="company", name="Company Library", url="https://example.com/library.git")


class LibraryTree:
    """A library repository laid out on disk."""

    def __init__(self, root: Path, source: Source = SOURCE) -> None:
        self.root = root
        self.source = source
        root.mkdir(parents=True, exist_ok=True)

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return path

    def part(self, ipn: str, text: str = PART) -> None:
        """Add a part, by default a copy of the standard one under a new number."""
        category = ipn.split("-")[0]
        self.write(f"parts/{category}/{ipn}.yaml", text.replace("1102-0001", ipn))

    def standard(self, *, model: bool = False) -> LibraryTree:
        """One resistor in which everything resolves.

        The symbol R_0603 extends R, which is where the body and pins are --
        the shape every real library has, and the one placement has to get
        right.
        """
        self.write("categories.yaml", CATEGORIES)
        self.write("mpns/RC0603FR-0710KL.yaml", MPN)
        self.write("parts/1102/1102-0001.yaml", PART)
        self.write("symbols/Passives.kicad_symdir/R.kicad_sym", symbol_file("R", units=2))
        self.write(
            "symbols/Passives.kicad_symdir/R_0603.kicad_sym",
            symbol_file("R_0603", extends="R", footprint="Passives:R_0603_1608Metric"),
        )
        self.write(
            "footprints/Passives.pretty/R_0603_1608Metric.kicad_mod",
            footprint_file("R_0603_1608Metric", model=MODEL_REF if model else None),
        )
        if model:
            self.write(MODEL_PATH, STEP)
        return self

    @property
    def checkout(self) -> Checkout:
        return Checkout(source=self.source, path=self.root, commit="abc1234def")

    def snapshot(self) -> Snapshot:
        return build_snapshot([self.checkout])


# -- an identity provider ---------------------------------------------------


class FakeProvider:
    """An OIDC provider that lives entirely inside an httpx transport.

    It signs real tokens with a real key and checks PKCE the way a real
    provider does, so what the tests exercise is this app's own client code
    and not a stand-in for it.
    """

    issuer = "https://idp.example.com"
    metadata_url = f"{issuer}/.well-known/openid-configuration"
    client_id = "kicad-library-manager"

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.key_id = "key-1"
        self.metadata = {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "userinfo_endpoint": f"{self.issuer}/userinfo",
            "jwks_uri": f"{self.issuer}/jwks",
        }
        self.userinfo: dict[str, dict] = {}  # access token -> claims
        self.codes: dict[str, dict] = {}  # authorization code -> what was asked for
        self.requests: list[httpx.Request] = []
        self.unreachable = False

    # -- what a test asks of it

    def client(self, **overrides) -> OidcClient:
        options = {
            "metadata_url": self.metadata_url,
            "client_id": self.client_id,
            "scopes": ("openid", "email"),
            "transport": httpx.MockTransport(self._handle),
        }
        return OidcClient(**{**options, **overrides})

    def access_token(self, subject: str = "user-1", *, key=None, **claims) -> str:
        """A signed JWT access token. Only `sub` unless more is asked for."""
        payload = {"iss": self.issuer, "sub": subject, "exp": int(time.time()) + 300, **claims}
        payload = {name: value for name, value in payload.items() if value is not None}
        return jwt.encode(payload, key or self.key, algorithm="ES256", headers={"kid": self.key_id})

    def opaque_token(self, subject: str = "user-1", **claims) -> str:
        """A token that means nothing without asking userinfo."""
        token = f"opaque-{len(self.userinfo)}"
        self.userinfo[token] = {"sub": subject, **claims}
        return token

    def approve(self, authorization_url: str, subject: str = "user-1", **claims) -> str:
        """Play the user approving a sign-in: returns the authorization code."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(authorization_url).query).items()}
        code = f"code-{len(self.codes)}"
        self.codes[code] = {**query, "token": self.access_token(subject, **claims)}
        return code

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    # -- being a provider

    def _jwks(self) -> dict:
        jwk = jwt.algorithms.ECAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**jwk, "kid": self.key_id, "use": "sig", "alg": "ES256"}]}

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.unreachable:
            raise httpx.ConnectError("no route to host", request=request)

        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json=self.metadata)
        if path == "/jwks":
            return httpx.Response(200, json=self._jwks())
        if path == "/userinfo":
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if token not in self.userinfo:
                return httpx.Response(401, json={"error": "invalid_token"})
            return httpx.Response(200, json=self.userinfo[token])
        if path == "/token":
            return self._token(request)
        return httpx.Response(404)

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        asked = self.codes.pop(form.get("code", ""), None)  # a code works once
        if asked is None:
            return httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "unknown code"}
            )

        digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        if (
            challenge != asked["code_challenge"]
            or form.get("redirect_uri") != asked["redirect_uri"]
        ):
            return httpx.Response(400, json={"error": "invalid_grant"})

        return httpx.Response(200, json={"access_token": asked["token"], "token_type": "Bearer"})
