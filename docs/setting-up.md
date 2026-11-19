# Setting it up

For whoever runs the server: from nothing to designers placing parts, and then
keeping it running. Every setting is described in [.env.example](../.env.example);
the [README](../README.md) says why each thing works the way it does.

- [What you need](#what-you-need)
- [1. The library's repository](#1-the-librarys-repository)
- [2. A first run, without sign-in](#2-a-first-run-without-sign-in)
- [3. Sign-in](#3-sign-in)
- [4. Writing to the library](#4-writing-to-the-library)
- [5. Keeping it current](#5-keeping-it-current)
- [6. Suppliers](#6-suppliers)
- [7. Telling the designers](#7-telling-the-designers)
- [Running it](#running-it)

## What you need

- A host that runs one Docker container, with a persistent volume.
- An HTTPS address for it, at the root of a domain or under a path. Plain http
  is enough to try it out; sign-in needs https.
- A git repository for the library, on GitHub, Gitea or any host that serves
  git over https.
- For sign-in: an OpenID Connect provider (Keycloak, Authentik, Auth0, Zitadel,
  Google, GitLab). Not GitHub's OAuth Apps.

The app keeps everything that cannot be rebuilt in one SQLite file on the
volume: the BOMs, who signed in, the HTTP library tokens. The library itself is
in git.

## 1. The library's repository

Make a repository with at least `categories.yaml`, one part, and the symbol and
footprint it names. [The library's files](library-files.md#where-things-go) has
the smallest library that works. An existing KiCad library can be added
alongside, as a second source; see [Several repositories](library-files.md#several-repositories).

If the repository is private, the app needs a token to read it (step 4).

## 2. A first run, without sign-in

Start with the library open, to see it work before adding sign-in.

```bash
cp .env.example .env
```

In `.env`, set at least:

```ini
PUBLIC_URL=https://library.example.com
AUTH_ENABLED=false
LIBRARY_SOURCES=[{"id":"company","name":"Company Library","url":"https://github.com/acme/kicad-library.git","ref":"main"}]
```

`PUBLIC_URL` is the address designers will use, exactly, with its path if the
app is under one (`https://tools.example.com/library`). Every address the app
hands out is built from it, and KiCad refuses a provider whose addresses do not
match where it found it. A proxy in front of the app strips the path before
passing a request on.

```bash
docker compose up --build
```

The app listens on port 8000 (`PORT` changes it), and keeps its data in the
volume mounted at `/data`. A bind mount instead of the named volume must be
writable by uid 1000.

Open `PUBLIC_URL` in a browser. The status page shows each source, *syncing*
until the first clone is read, then how many parts it has. Everything it finds
wrong with the configuration is listed there. Open **Issues** for what is wrong
with the library itself.

**On Coolify:** the Dockerfile build pack, port 8000, health check `/healthz`,
a volume at `/data`, and the variables in the service's environment. Keys and
tokens go there and nowhere else: not in the repository, not in a ticket.

Health checks: `/healthz` says the process is up and nothing more, so an
unreachable git host does not restart the container. `/readyz` says whether
there is a library to serve.

## 3. Sign-in

With `AUTH_ENABLED=true` nothing that reads the library is answered without a
session. Register **one client** at the identity provider:

| | |
| --- | --- |
| Client type | Public (no secret), with PKCE (S256) |
| Redirect URIs | `http://127.0.0.1/oauth/callback` on **any port** (KiCad's), and `<PUBLIC_URL>/auth/callback` (the browser's) |
| Signing key | Elliptic curve, ES256 |
| Scopes | `openid` and `email` only |
| Claims | One naming the user, such as `preferred_username`. No groups, no full profile |

KiCad signs in itself, through the system browser, and listens for the answer
on a port it picks each time; how a provider writes "any port" differs, so look
up loopback redirects in its documentation. The second address is for a part's
page opened from a placed symbol's Datasheet link, in an ordinary browser.

The token limit matters. KiCad keeps its tokens in the operating system's
credential store, which on Windows holds about 1280 characters for all three.
A provider that signs with RSA or puts names and groups in the tokens goes
over, and KiCad fails *after* a successful login with "Failed to store remote
provider tokens securely". See [Keep the tokens small](../README.md#keep-the-tokens-small).

Then:

```ini
AUTH_ENABLED=true
OIDC_METADATA_URL=https://idp.example.com/realms/acme/.well-known/openid-configuration
OIDC_CLIENT_ID=kicad-library-manager
LIBRARIANS=ada@example.com,grace@example.com
```

`LIBRARIANS` lists who may decide what goes into the library, by the username,
e-mail address or subject id the provider reports. Not by display name, which
anyone may change.

Restart, and check the status page: it prints the exact redirect address to
register, and lists what it finds wrong with the sign-in settings.

## 4. Writing to the library

Without a write token the app only reads the library: nobody can request a
part, upload a footprint or edit a template in it. Make a token that may push
to the library's repository (on GitHub, a fine-grained token for that one
repository, *Contents: read and write*) and set it:

```ini
LIBRARY_WRITE_TOKEN=github_pat_...
```

Every change made in the app is then a commit authored by whoever made it, and
committed by the app.

For a private source the write token is also used to read it. To read with a
different token, or read a private source the app does not write to, mark the
source `"private": true` and set `LIBRARY_READ_TOKEN`. A token is only sent to
sources marked private, over https.

## 5. Keeping it current

The app looks at the repository again every 15 minutes
(`LIBRARY_REFRESH_MINUTES`). For a change pushed by hand to show within
seconds, add a webhook:

1. Make up a long random secret and set it as `WEBHOOK_SECRET`.
2. At the git host, add a webhook for *push* events to
   `<PUBLIC_URL>/api/v1/library/webhook`, content type `application/json`,
   with the same secret.

GitHub and Gitea sign their calls the way the app checks. A librarian can also
press **Refresh now** on the status page.

## 6. Suppliers

A part's page and a BOM's costing show stock and prices from the suppliers the
app has keys for. Each is optional:

| Supplier | Register | Set |
| --- | --- | --- |
| DigiKey | an application at developer.digikey.com, subscribed to *Product Information v4* | `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET`, and `DIGIKEY_LOCALE_*` for your country and currency |
| Mouser | the *Search API* at Mouser's API hub | `MOUSER_API_KEY` |
| Farnell | the *Product Search API* at partner.element14.com | `FARNELL_API_KEY`, and `FARNELL_STORE` for your country |

Each allows so many calls a day, and the app stops itself before that
(`*_DAILY_LIMIT`, 1000 each). Answers are kept for a day
(`SUPPLIER_CACHE_HOURS`). The status page shows the calls made today, and why
a supplier last refused, in its own words. See [Suppliers](../README.md#suppliers).

## 7. Telling the designers

Send them [Using the library](designers.md) and two addresses:

- for KiCad 10's Remote Symbols panel:
  `<PUBLIC_URL>/.well-known/kicad-remote-provider`;
- for KiCad's HTTP library: `<PUBLIC_URL>/kicad-library`, where each makes a
  token and downloads the library file.

If you changed `REMOTE_LIBRARY_PREFIX`, tell them the prefix to set in KiCad's
Remote Symbol settings.

## Running it

**Backups.** The app copies its database once a day to `backups/` beside it,
and keeps 14 (`BACKUP_KEEP`). Those copies are on the same volume. Also back up
the volume if the host can, and download a copy from the status page now and
then to keep elsewhere. The status page says when the last copy was made.

**Restoring.** Stop the app, replace the database file (`DATABASE_PATH`,
`/data/library-manager.db` by default) with a copy, and start it. Everyone signs
in again. The HTTP library tokens are as they were in the copy: one made since
is gone, and one revoked since works again, so revoke it once more.

**Upgrading.** Deploy the new image. Any new table or column is added the first
time the new version opens the database; nothing is run by hand. Going back a
version after that is refused, and reported: restore the copy from before the
upgrade, or go forward again. The library's clones can always be deleted; they
are made again.

**One instance.** Run one container. The index and the stores are not built to
be shared between two.

**Logs.** `LOG_LEVEL` and `LOG_FORMAT=json`. Every answer carries an
`X-Request-ID`, which is on every log line the request wrote: ask for it when
someone reports an error.

**Tokens and keys.** Rotate the library token at the git host and set the new
one; nothing else holds it. When someone leaves, a librarian revokes their HTTP
library tokens on the KiCad library page, and you disable them at the
identity provider. A session already open lasts until it runs out
(`SESSION_TTL_SECONDS`, eight hours).
