# Working on the app

For whoever changes the code. The [README's Development section](../README.md#development)
has the commands and the layout; this is how to run it against a library of
your own, how the tests are built, and where each kind of change goes.

- [Running it locally](#running-it-locally)
- [The tests](#the-tests)
- [Where a change goes](#where-a-change-goes)
- [Conventions](#conventions)

## Running it locally

Python 3.12.

```bash
python -m venv .venv
```

```bash
.venv/Scripts/pip install -r requirements-dev.txt
```

(`.venv/bin/pip` outside Windows.) The app needs a library to serve. A bare
git repository on your own disk will do, and lets you try changes without
pushing anywhere:

```bash
git clone --bare https://github.com/acme/kicad-library.git /work/demo-lib.git
```

Then set, in the environment or `.env`:

```ini
PUBLIC_URL=http://localhost:8000
AUTH_ENABLED=false
LIBRARY_SOURCES=[{"id":"company","name":"Company Library","url":"/work/demo-lib.git"}]
LIBRARY_WORKDIR=./.work/sources
DATABASE_PATH=./.work/library-manager.db
LIBRARY_WRITE_TOKEN=local
```

Give the repository's address as an absolute path. With sign-in off, everyone
is a librarian. A write token of any value lets the
app commit to the local repository, so the forms can be tried; nothing leaves
your disk.

```bash
uvicorn app.main:app --reload
```

To see the panel as KiCad shows it, add `http://localhost:8000/.well-known/kicad-remote-provider`
in KiCad 10; `ALLOW_INSECURE_LOCALHOST` (on by default) lets KiCad accept http
on localhost.

## The tests

```bash
pytest -q
```

Everything runs in-process. A test builds the deployment it wants from
settings, rather than patching the environment, and the library is a
directory of files written by the test:

- `tree` is a `LibraryTree` (in `tests/support.py`): a library on disk. Its
  `standard()` writes a small library of categories, one resistor, its symbol,
  footprint and manufacturer part; `part()`, `write()` and the like add to it.
  `tree.snapshot()` indexes it without an app, for testing the library layer
  alone.
- `make_client(tree, **settings)` builds the app for that library and a
  `TestClient` for it, and waits until the library is indexed. Settings are
  given as environment variable names: `make_client(tree, **OPEN)` for sign-in
  off.
- `sign_in(client)` and `sign_in_librarian(client)` sign the client in as a
  designer (Ada Lovelace) or a librarian (Grace Hopper), through a fake identity
  provider. Switch between them on one client rather than building a second
  app on the same directory.
- `tests/support.py` also has `symbol_file()` and `footprint_file()`, for
  KiCad files with what a test needs in them.

Some tests hold the code as a whole to its rules:

- `test_layers.py`: each package imports only from those below it.
- `test_api_contract.py`: every JSON endpoint names its answer's model.
- `test_web_provider.py`: the discovery document matches KiCad's schema,
  copied in `tests/fixtures/`.
- `test_bridge_js.py`: the scripts that talk to KiCad, against a pretend KiCad
  in `tests/js/`. Skipped without Node.

CI runs ruff, the format check, mypy and the tests, then builds the image and
checks that it starts.

## Where a change goes

Each package only reaches downwards (`test_layers.py` holds it to that):

```
web  →  services  →  boms, suppliers, auth  →  library  →  domain, kicad
                                                 db, errors
```

**A rule about parts** (what a part must say, who may change it) goes in
`app/domain/`, as a function that is given what it needs and does no I/O:
`rules.py` for what a part must say, `policy.py` for who may do what. Both the
forms and the library's own checks use it.

**A new check of the library's files** goes where that kind of file is read:
`app/library/parts.py` for the YAML files, `app/library/assets.py` for KiCad's.
Report it as `Issue.error(kind, message, path)` or `Issue.warning(...)`. The
kind is a short slug the issues page groups by, and is not to be renamed once
released. Add it to the table in [docs/library-files.md](library-files.md#what-is-checked).

**Something a person can do** is one function in `app/services/`: who may do
it, whether what was asked makes sense, the change, and what came of it. It
raises one of the errors in `app/errors.py`; `app/web/errors.py` turns each
into a status code, so neither a service nor a route catches an error to
answer it.

**A change to the library's files** goes through `services/changes.publish`,
given a function that writes the files and returns the commit's subject. It
commits in the person's name, with their note as the body, and pushes, again
on top if someone pushed first. Write the files through `app/library/files.py`,
which is what the loaders read through.

**A JSON endpoint** is a route in `app/web/routes/` that turns the request into
one service call and the outcome into a model from `app/web/schemas/`, built by
that model's `of()`. Declare `response_model`, or `test_api_contract.py` fails.
Add it to the README's table of endpoints.

**A page** is a Jinja template in `app/templates/` extending `base.html`, and
one ES module in `app/static/js/` importing what it needs from `app.js`. There
is no build step. A page that only makes sense in a browser marks what leads to
it `web-only`, which hides it inside KiCad's panel. The look is a plain
catalogue: monochrome, rules and type, the classes already in `app.css`. No
coloured badges, cards or accent colours.

**Something kept in the database** gets a numbered step in
`app/db/migrations.py`, appended to `MIGRATIONS`, and a store of its own that
takes the `Database`. Never change a step that has shipped: add another. Decide
whether a backup should carry it (`app/db/backup.py` leaves out sessions and
supplier answers).

**A setting** is a field of `Settings` in `app/config.py`, read in `from_env`
with a default that works, and described in `.env.example`. A value that cannot
be read falls back to its default and is reported on the status page, never
stops the app from starting.

**A supplier** is a class in `app/suppliers/`; see
[Another supplier](../README.md#another-supplier).

## Conventions

- `ruff check`, `ruff format` (lines of 100), and `mypy` in strict mode, all
  clean.
- Docstrings and comments say why, in sentences. What the code does, the code
  says.
- Messages a person reads say what is wrong and what to do, in plain words:
  "the footprint library Passives is not in fp-lib-table, so it is missing for
  whoever uses the library checked out".
- Supplier keys and tokens live in the deployment's environment. Never in the
  repository, a test, a log line or an error message; the HTTP client's request
  log is turned down in `app/observability.py` for that reason, since Mouser
  puts its key in the address.
- Commit messages are one sentence saying what the change does, in the
  imperative: "Upload symbols, footprints and 3D models, and spot duplicate
  parts".
