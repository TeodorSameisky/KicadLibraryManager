# KiCad Library Manager

Serves KiCad symbols, footprints and 3D models to the **Remote Symbols** panel in
KiCad 10 and later.

KiCad embeds a WebView, loads this app's `/panel` page inside it, and exchanges
JSON-RPC messages with the page over `window.kiclient.postMessage`. Assets are
pushed into KiCad from the page rather than fetched by it.

## Status

Authentication and the provider handshake are implemented. The library indexer
is not — the catalog is currently empty.

| Path | Purpose |
| --- | --- |
| `/.well-known/kicad-remote-provider` | Discovery document KiCad reads first |
| `/panel` | The page KiCad renders in its WebView |
| `/api/v1/session/bootstrap` | Exchanges an access token for a single-use nonce |
| `/session/consume?n=` | Redeems a nonce, sets the session cookie |
| `/api/v1/session/me` | Current session |
| `/api/v1/session/logout` | Clears the session |
| `/api/v1/status` | Index state, per source |
| `/api/v1/parts` | Part search |
| `/api/v1/parts/{ipn}` | One part with its approved sources |
| `/api/v1/parts/{ipn}/assets` | The payload KiCad places |
| `/api/v1/parts/{ipn}/symbol.svg` | Symbol preview |
| `/api/v1/parts/{ipn}/footprint.svg` | Land pattern preview |

Previews are drawn from the parsed files rather than by shelling out to
KiCad. The part page inlines them so that hovering a pin highlights the pad
it maps to, and the other way round; the standalone endpoints are used for
the panel's thumbnails, where no interaction is needed.
| `/ipn/{ipn}` | Part page; the Datasheet target of placed symbols |
| `/healthz` | Liveness probe |

## The library

Parts come from one or more git repositories, configured as JSON in
`LIBRARY_SOURCES`. Several are supported because a company library, the
official KiCad libraries and a vendor library are separate repositories.

Clones live under `LIBRARY_WORKDIR`, which **must be a persistent volume** --
re-cloning a library with 3D models on every deploy is slow and pointless.

Indexing runs in the background at startup. Cloning takes long enough that
doing it inline would fail the container health check, so the app serves a
documented `syncing` state until the first index lands.

Only **IPNs** are placed in KiCad. An IPN names a symbol and a footprint,
carries field values, and lists the approved manufacturer parts; MPNs are
separate documents because one can satisfy several IPNs.

## Placing a part

A library symbol is a template. What gets placed is an IPN, so the symbol is
rewritten on the way out: it carries the internal part number, the part's
field values, the preferred manufacturer part, and a `Datasheet` pointing at
that part's page.

The payload contains the symbol *and every ancestor it extends*. KiCad
resolves `extends` within the library it is handed, so a derived symbol sent
alone places a part with no pins and no body.

Assets travel inline in the RPC envelope, zstd-compressed and base64-encoded,
in a fixed order: footprint and 3D model first with mode `SAVE`, the symbol
last with mode `PLACE`. A part whose symbol cannot be completed is refused
rather than placed broken.

Large 3D models are the weak point of this transport -- a multi-megabyte STEP
file crossing a WebView string bridge can exceed KiCad's response timeout. The
bundle warns when a model is big enough to be at risk.

## Run locally

```bash
cp .env.example .env
docker compose up --build
```

Without Docker:

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
pytest -q
```

## Authentication

KiCad is the OAuth2 **client**, not this server. It runs Authorization Code +
PKCE itself: it starts a loopback listener on `127.0.0.1/oauth/callback`,
launches the user's system browser, and exchanges the code at the identity
provider's token endpoint. This app never sees credentials — it only verifies
the resulting access token and converts it into a browser session for the panel.

```
KiCad ──discovery──▶ /.well-known/kicad-remote-provider
KiCad ──OAuth2 + PKCE──▶ your IdP ──access token──▶ KiCad
KiCad ──POST {access_token, next_url}──▶ /api/v1/session/bootstrap
      ◀── {nonce_url} ──
WebView ──GET──▶ /session/consume?n=…  ──Set-Cookie, 303──▶ /panel
```

Any OIDC provider works, configured via `OIDC_METADATA_URL` and
`OIDC_CLIENT_ID`. Both JWT access tokens (verified locally against the
provider's JWKS) and opaque tokens (resolved via `userinfo_endpoint`) are
supported.

### Keep the requested scopes small

KiCad persists its token bundle (access + refresh + id_token) in the OS
credential store. On Windows that is the Credential Manager, capped at 2560
bytes -- about 1280 characters, since it stores UTF-16. An oversized bundle
fails *after* a successful login with "Failed to store remote provider tokens
securely".

authentik's `profile` scope includes the user's group list and can push the
bundle past that limit, so the default is `openid,email`. Drop to `openid`
alone if it still fails; the panel then shows the subject id instead of a name.

The provider must allow **PKCE on a public client** and a **dynamic 127.0.0.1
loopback redirect URI**, since KiCad picks an ephemeral port. Keycloak,
Authentik, Auth0, Zitadel, Google and GitLab all qualify. **GitHub OAuth Apps do
not** — they publish no RFC 8414 metadata and do not support PKCE.

Setting `AUTH_ENABLED=false` advertises auth type `none`, which leaves the panel
and every asset readable by anyone who can reach the URL.

### Security headers

`Strict-Transport-Security` is set by the app rather than the proxy, since
Coolify has no HSTS toggle and hand-written Traefik labels are overwritten on
redeploy. It is emitted only on HTTPS requests, honouring `X-Forwarded-Proto`
behind a terminating proxy. `HSTS_PRELOAD` stays off by default: preload list
entries are effectively irreversible.

### TLS is required

OAuth2 and `Secure` cookies do not work over plain HTTP, and KiCad validates
URL security during the bootstrap exchange. `PUBLIC_URL` must be `https://` in
production.

## Deployment (Coolify)

Built from the `Dockerfile` build pack; pushing to `main` triggers a redeploy.

- Container port **8000** (or set `PORT`)
- Health check path `/healthz`
- **Set `PUBLIC_URL`** to the deployed origin — it defaults to
  `http://localhost:8000`, which produces a discovery document KiCad will reject.

See [.env.example](.env.example) for all settings.

## Tests

```bash
pytest -q
```

The discovery document is validated against KiCad's own published JSON schema, a
verbatim copy of which lives in `tests/fixtures/`. Refresh that fixture when
targeting a newer KiCad release.
