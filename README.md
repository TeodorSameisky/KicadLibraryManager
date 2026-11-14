# KiCad Library Manager

Serves an internal part library to the **Remote Symbols** panel in KiCad 10 and
later.

The library lives in git. This app indexes it, shows it as a searchable catalog
inside KiCad, and places parts on the schematic: symbol, footprint and 3D model
together, with the internal part number and a link back to the part's page.

It also keeps the BOMs of the projects built from that library, so that each
BOM can be read against the library as it stands today, and each part can say
where it is used.

- [How it works](#how-it-works)
- [The library](#the-library)
- [BOMs and where used](#boms-and-where-used)
- [Configuration](#configuration)
- [Authentication](#authentication)
- [Deployment](#deployment)
- [Endpoints](#endpoints)
- [Development](#development)

## How it works

KiCad embeds a WebView, loads this app's `/panel` page in it, and exchanges
JSON-RPC messages with the page over `window.kiclient.postMessage`. Assets are
pushed into KiCad from the page; KiCad does not fetch them.

```
KiCad ──GET──▶ /.well-known/kicad-remote-provider      what this provider offers
KiCad ──loads─▶ /panel  (in its WebView)
        ◀─ NEW_SESSION ─▶                               the RPC session opens
panel ──GET──▶ /api/v1/parts/{ipn}/assets               on "Place"
panel ──DL_FOOTPRINT, DL_3DMODEL, DL_SYMBOL──▶ KiCad    saved, saved, placed
```

### What gets placed

A library symbol is a template. What lands on the schematic is an **IPN**, so
the symbol is rewritten on its way out. It carries the internal part number, the
part's field values, the preferred manufacturer part, and a `Datasheet` field
pointing at the part's page on this app.

Three things about that payload are dictated by how KiCad receives it:

- **One self-contained symbol.** KiCad saves exactly one symbol per payload, so
  a symbol that `extends` another is flattened: the ancestor's body, the
  descendant's properties, no `extends` left.
- **No unit 0.** KiCad's remote receive path drops symbols that use the
  "common to all units" unit, and the part lands with no graphics. Unit 0 is
  folded into each real unit before sending.
- **The footprint names KiCad's library, not ours.** KiCad files what it
  receives under `<prefix>_<library>` (`remote_passives`), so that is what the
  symbol's `Footprint` field says. `REMOTE_LIBRARY_PREFIX` must match the
  prefix in KiCad's Remote Symbol Settings.

Assets travel inline in the RPC envelope, zstd-compressed and base64-encoded:
footprint and 3D model first with mode `SAVE`, the symbol last with mode
`PLACE`. A part whose symbol cannot be completed is refused rather than placed
broken; a missing footprint or model only warns.

A multi-megabyte STEP file crossing a WebView string bridge can exceed KiCad's
response timeout. The bundle warns when a model is large enough to be at risk.

### Previews

Symbols and footprints are drawn as SVG from the parsed files, rather than by
installing KiCad in the container to draw a resistor. The part page inlines
them, so hovering a pin highlights the pad it maps to and the other way round.

3D models are STEP, which describes surfaces rather than triangles, so showing
one needs a CAD kernel. That kernel runs in the browser: `occt-import-js` is
OpenCascade compiled to WebAssembly. It is 7.6 MB and is fetched only when a
viewer is opened. See [app/static/vendor](app/static/vendor/README.md).

## The library

Parts come from one or more git repositories, listed in `LIBRARY_SOURCES`.
Several are supported because a company library, the official KiCad libraries
and a vendor's are separate repositories; a symbol in one may name a footprint
in another.

```
categories.yaml                              the category codes an IPN may start with
parts/<category>/<IPN>.yaml                  an internal part: 1102-0001
mpns/<MPN>.yaml                              a manufacturer part
symbols/<Library>.kicad_symdir/<Name>.kicad_sym
footprints/<Library>.pretty/<Name>.kicad_mod
3dmodels/<Library>.3dshapes/<Name>.step
```

The asset directories may also sit at the repository root, the way KiCad's own
libraries are laid out.

```yaml
# parts/1102/1102-0001.yaml
ipn: 1102-0001
description: Resistor 10k 1% 0.1W 0603
status: approved            # draft | approved | obsolete
symbol: Passives:R_0603
footprint: Passives:R_0603_1608Metric
fields:
  Value: 10k
  Tolerance: 1%
mpns:
  - mpn: RC0603FR-0710KL
    preferred: true
  - mpn: CRCW060310K0FKEA

# mpns/RC0603FR-0710KL.yaml
mpn: RC0603FR-0710KL
manufacturer: Yageo
datasheet: https://example.com/datasheet.pdf
lifecycle: active           # active | nrnd | obsolete | unknown
```

Only IPNs are placed. An IPN is `<category>-<number>`, names a symbol and a
footprint, and lists the manufacturer parts approved for it. MPNs are documents
of their own because one can satisfy several IPNs — which is what makes "what
does this discontinuation reach?" answerable.

IPNs are one numbering scheme, so they are read from one repository: the first
source that has a `parts/` directory.

### Indexing

The index is built in the background at startup. Cloning takes long enough
that waiting for it would fail the container health check, so the app answers
at once and reports `syncing` until the first index lands. Clones are shallow
and live under `LIBRARY_WORKDIR`, which **must be a persistent volume**.

If a source cannot be reached later, the previous index keeps being served and
the failure is shown on the status page.

Everything that does not line up — a footprint no source has, a symbol whose
parent is missing, an IPN in an undeclared category — is collected and listed
at `/issues`, grouped by kind, with the repository-relative path to open.

## BOMs and where used

Export a project's BOM from KiCad's schematic editor (Tools → Generate Bill of
Materials) and upload it at `/boms`, with the project's name and, if it has
one, a revision.

**How parts are recognised.** By an `IPN` column if the export has one, and
otherwise by the `Datasheet` column: a symbol placed from this library has its
part's page there, and that address ends in the IPN. KiCad's default BOM
columns include Datasheet, so an export with nothing changed is enough. The
layout is otherwise the user's: columns are found by their headings, the
delimiter may be a comma, a semicolon or a tab, rows may be grouped or one per
reference, and ranges like `R1-R4` are opened out. Rows that are not library
parts — mounting holes, test points — are kept, without a part number.

**What a stored BOM shows.** Every line with the part's description, its
preferred manufacturer part and manufacturer, and a remark where something
needs attention: a part that is obsolete or still a draft, a part the library
does not have, a source that is obsolete or not for new designs. Lines are
looked up when the BOM is read, not when it was uploaded, so a part made
obsolete last week shows on a BOM from last year. The BOM can be downloaded
with those columns added, for purchasing, or as it was uploaded.

**Where used.** Each part's page lists the stored BOMs the part is on, with
the quantity and the reference designators. That is what decides whether a
part can be changed or retired.

One BOM is kept per project and revision. Uploading the same revision again
replaces it, after asking.

### Where BOMs are kept

The library is in git because it is authored and reviewed. BOMs are records of
what was built and are looked up by part, so they are in a database: one
SQLite file at `DATABASE_PATH`, by default `/data/library-manager.db`, next to
the library's clones.

**That file is the one thing here that cannot be rebuilt from somewhere else.**
The clones can be cloned again; the BOMs cannot be uploaded again by anyone who
no longer has them. Back up the data volume. The original upload is stored
whole beside the lines read from it.

The file is created on first use. The directory it is in is not: a path that
points somewhere that does not exist is reported on the status page instead.

## Configuration

Everything is set through environment variables; [.env.example](.env.example)
lists them all with their reasons. The ones a deployment needs:

| Variable | |
| --- | --- |
| `PUBLIC_URL` | The externally reachable address, including any path prefix. Every URL the app hands out is derived from it. |
| `LIBRARY_SOURCES` | JSON array of `{id, name, url, ref}` git repositories. |
| `LIBRARY_WORKDIR` | Where the clones are kept. A volume. |
| `DATABASE_PATH` | The SQLite file that holds the BOMs. On the same volume, and the thing to back up. |
| `AUTH_ENABLED`, `OIDC_METADATA_URL`, `OIDC_CLIENT_ID` | Sign-in; see below. |
| `REMOTE_LIBRARY_PREFIX` | Must match the library prefix in KiCad's Remote Symbol Settings. Default `remote`. |

A malformed value never stops the app from starting: it falls back to its
default and is reported. The status page at `/` shows every configuration
problem the app can detect, because nobody reads container logs to find out
why a login does nothing.

## Authentication

With `AUTH_ENABLED=true` the library is closed: every endpoint that reads it
needs a session. Any OIDC provider works. There are two ways to get a session,
because there are two kinds of client, and in both the user's credentials stay
between them and the identity provider.

### From KiCad

KiCad is the OAuth2 client. It runs Authorization Code + PKCE itself, through
the system browser and a loopback listener, and hands this app the resulting
access token:

```
KiCad ──OAuth2 + PKCE──▶ identity provider ──access token──▶ KiCad
KiCad ──POST {access_token, next_url}──▶ /api/v1/session/bootstrap
      ◀── {nonce_url} ──
WebView ──GET──▶ /session/consume?n=…   Set-Cookie, 303 to the panel
```

The app verifies the token — JWTs against the provider's published keys, opaque
tokens by asking its userinfo endpoint — and exchanges it for a single-use
nonce that the WebView turns into a session cookie.

The provider must allow **PKCE on a public client** and a **loopback redirect
on any port**: `http://127.0.0.1:<port>/oauth/callback`. Keycloak, Authentik,
Auth0, Zitadel, Google and GitLab qualify. GitHub OAuth Apps do not.

### From a browser

A placed symbol's `Datasheet` field points at `/ipn/{ipn}`, and that link is
followed in an ordinary browser — from the schematic, or from a PDF years
later with no KiCad running. The WebView's cookie never reaches that browser.
So the browser signs in with **this app as the OAuth2 client**:

```
Browser ──GET──▶ /auth/login?next=/ipn/1102-0001      307 to the provider
        ──GET──▶ /auth/callback?code=…&state=…         Set-Cookie, 303 to the part
```

It lands back on the part that was asked for. The PKCE verifier stays on the
server, and `state` works once whether or not the attempt succeeds.

This needs one more redirect URI registered at the provider, for the same
client:

```
<PUBLIC_URL>/auth/callback
```

The status page prints the exact value. A public client needs no secret;
`OIDC_CLIENT_SECRET` is honoured if you registered a confidential one.
`WEB_LOGIN_ENABLED=false` turns this off.

### Keep the tokens small

KiCad stores its token bundle (access, refresh and ID token) in the OS
credential store. On Windows that holds 2560 bytes — about 1280 characters. A
bundle over the limit fails *after* a successful login with "Failed to store
remote provider tokens securely".

Two things keep it under:

- **Sign tokens with an elliptic-curve key** (ES256). RSA signatures are four
  times the size.
- **Put as few claims in the tokens as possible.** Request only the scopes you
  need (`OIDC_SCOPES`, default `openid,email`), and check what your provider's
  scope mappings actually emit: a `profile` mapping that includes names and
  group lists will blow the limit on its own. One claim naming the user
  (`preferred_username`) is enough for the panel to show who is signed in.

If the access token names nobody, the app asks the provider's userinfo endpoint
before falling back to a shortened subject id.

### What stays open

Health probes have no session; the discovery document is what tells KiCad how
to authenticate at all; `/panel` carries the sign-in button; `/` is where a
broken sign-in gets diagnosed, and shows counts and configuration only. A
refused request is answered in the shape its caller can use: JSON for a script,
a page with a way in for a browser.

With `AUTH_ENABLED=false` the provider advertises auth type `none` and anyone
who can reach the address can read the library.

## Deployment

One container, built from the `Dockerfile`.

```bash
cp .env.example .env
```

```bash
docker compose up --build
```

- The app listens on **8000** (`PORT` changes it).
- Health check path: `/healthz`. It reports the process, not the library, so an
  unreachable git host never restarts the container. `/readyz` reports whether
  there is a catalog to serve.
- Mount a volume at `/data`. A named volume inherits the image's ownership; a
  bind mount keeps the host's and has to be writable by uid 1000. It holds the
  library's clones, which can be rebuilt, and the BOM database, which cannot:
  back it up.
- `PUBLIC_URL` must be `https://` whenever sign-in is on. OAuth2 and `Secure`
  cookies do not work over plain http.
- The app sets `Strict-Transport-Security` itself, on HTTPS requests only
  (`X-Forwarded-Proto` is honoured behind a proxy that terminates TLS).
- Sessions are held in memory: run one instance, and expect a restart to sign
  everyone out.

On Coolify: use the Dockerfile build pack, set the port to 8000 and the health
check to `/healthz`, add a volume at `/data`, and set the environment
variables. A push to the deployed branch redeploys.

Then, in KiCad: Preferences → Remote Symbols → add
`<PUBLIC_URL>/.well-known/kicad-remote-provider`.

### Logs

`LOG_LEVEL` sets verbosity; `LOG_FORMAT=json` writes one JSON object per line.
Every response carries an `X-Request-ID`, which is also on every log line the
request produced, so a report can be tied to the lines that describe it. An
unhandled error is logged in full and answered with nothing but that id.

## Endpoints

| Path | | Open |
| --- | --- | --- |
| `/.well-known/kicad-remote-provider` | Discovery document KiCad reads first | ✓ |
| `/panel` | The catalog, as KiCad embeds it | ✓ |
| `/` | Status: sources, counts, configuration problems | ✓ |
| `/healthz`, `/readyz` | Liveness, readiness | ✓ |
| `POST /api/v1/session/bootstrap` | KiCad's access token for a single-use nonce | ✓ |
| `/session/consume?n=` | Redeems a nonce, sets the session cookie | ✓ |
| `/auth/login?next=`, `/auth/callback` | Browser sign-in | ✓ |
| `/api/v1/session/me`, `POST /api/v1/session/logout` | The current session | ✓ |
| `/api/v1/status` | Index state per source, and the filters the catalog offers | |
| `/api/v1/parts?q=&status=&category=&limit=` | Part search | |
| `/api/v1/parts/{ipn}` | One part, with its sources | |
| `/api/v1/parts/{ipn}/assets` | What KiCad is sent to place it | |
| `/api/v1/parts/{ipn}/symbol.svg`, `/footprint.svg`, `/model.step` | Drawings and the 3D model | |
| `/api/v1/issues?severity=&limit=` | Everything that does not line up | |
| `/api/v1/boms` | `GET` the stored BOMs; `POST` one as JSON `{project, revision, note, filename, content, replace}` | |
| `/api/v1/boms/{id}` | `GET` a BOM against the library; `DELETE` it | |
| `/api/v1/boms/{id}/export.csv`, `/source.csv` | The BOM with library columns added; the file as uploaded | |
| `/api/v1/parts/{ipn}/where-used` | The stored BOMs a part is on | |
| `/ipn/{ipn}` | The part's page; where `Datasheet` points | |
| `/boms`, `/boms/{id}` | Uploading BOMs; one BOM against the library | |
| `/issues` | The issue list | |
| `/docs` | Interactive API reference | ✓ |

## Development

```bash
pip install -r requirements-dev.txt
```

```bash
uvicorn app.main:app --reload
```

```bash
pytest -q
```

```bash
ruff check app tests
```

```bash
ruff format app tests
```

CI runs the same checks, then builds the image and calls it: the image is what
ships, and a Dockerfile that has stopped building is otherwise found on deploy.

### Layout

Each package only reaches downwards.

```
app/
  main.py            the entry point: uvicorn app.main:app
  application.py     create_app(): assembles everything below
  config.py          Settings, read once from the environment
  observability.py   logging, request ids, crash handling
  security.py        HSTS and hardening headers
  web/               HTTP: routes, pages, request-scoped dependencies
  auth/              the OIDC provider, sessions, safe redirects
  boms/              reading KiCad BOMs, keeping them, setting them against the library
  library/           sources, assets, parts, snapshots, placement, previews
  kicad/             the file formats: reading, rewriting, drawing
  templates/  static/
tests/
  support.py         library files, a library on disk, a fake identity provider
```

`kicad/` knows nothing about parts or HTTP; `library/` and `boms/` know nothing
about HTTP. The app is built by a factory from a `Settings` object, so a test builds
the deployment it wants instead of patching the environment.

The reader for KiCad's files is deliberately not a model of the format: a file
is a generic tree, so a token a newer KiCad adds is just another node. The
writer reproduces KiCad's own layout, so a served file is indistinguishable
from one KiCad saved.

The discovery document is validated against KiCad's own JSON schema, a verbatim
copy of which is in `tests/fixtures/`. Refresh it when targeting a newer KiCad.
