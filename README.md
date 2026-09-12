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
| `/healthz` | Liveness probe |

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
