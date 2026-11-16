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
- [Finding a part](#finding-a-part)
- [Requesting and approving parts](#requesting-and-approving-parts)
- [BOMs and where used](#boms-and-where-used)
- [Suppliers](#suppliers)
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
manufacturers.yaml                           the manufacturers, each under one name (optional)
templates/<category>.yaml                    the fields a part in that category carries
products/<family>.yaml                       a product family and the devices in it
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

### Templates

A resistor without a resistance is not a part anyone can choose. A category's
template says what every part in it has to say about itself, once, instead of
each part being left to remember it. It is a file in the library, next to the
parts it describes, so it is reviewed and versioned the same way:

```yaml
# templates/1102.yaml
category: "1102"          # optional; the file name is what counts
value: "{Resistance}"     # what KiCad's Value field shows, built from the fields
fields:
  - name: Resistance
    required: true
    example: 10k          # shown in the empty field
  - name: Tolerance
    required: true
    options: [0.1%, 1%, 5%]   # the only values allowed
    default: 1%               # what a new part starts with
  - name: Power
    example: 0.1W
  - Package               # the shorthand: offered, nothing demanded
```

- **The request form lays the template out.** Choosing a category shows its
  fields, required ones marked, with a dropdown wherever `options` are given.
  The server holds a request to the template too, so a script is held to it
  as well, and says everything that is missing at once.
- **Fields the template does not name are still allowed.** A template says
  what a part must have, not everything it may.
- **`value` fills the placed symbol's Value.** A part that carries its
  resistance as `Resistance` has no `Value` of its own, and would show the
  symbol's name on the sheet. The pattern is names in braces and nothing more.
  A part that sets its own `Value` keeps it.
- **Existing parts are checked too.** One that falls short of its category's
  template is listed on the issues page as a warning, which is the worklist
  when a template is added to a library that already has parts. It still
  places: a part that predates a rule is not unusable.

A category with no template asks nothing, so templates can be introduced one
category at a time.

**Librarians edit templates in the app**, at `/templates`: every category is
listed with how many fields its template has and how many of its parts fall
short of it, and each opens an editor for its fields, their order, which are
required, the values they are limited to, and the Value pattern. Saving is one
commit to the repository, in the librarian's name, like any other change. The
editor says at once how many existing parts the change leaves short, with a link
to which. A template can also be removed there, after which its category asks
nothing of its parts.

Writing a template here lays the file out its own way, so a comment in a
hand-written one does not survive being edited in the app. Numbers like `0805`
are written quoted: a YAML reader that follows other rules would otherwise take
them for 805. `IPN`, `Datasheet`, `Footprint`, `Description`, `MPN` and
`Manufacturer` are set from the part itself and cannot be template fields.

Only IPNs are placed. An IPN is `<category>-<number>`, names a symbol and a
footprint, and lists the manufacturer parts approved for it. MPNs are documents
of their own because one can satisfy several IPNs — which is what makes "what
does this discontinuation reach?" answerable.

IPNs are one numbering scheme, so they are read from one repository: the first
source that has a `parts/` directory.

### Manufacturers

The same company gets written "Texas Instruments", "TI" and "Texas Instruments
Inc." by three people on three days. To a reader those are one manufacturer;
to anything that looks a part up by who makes it they are three. So a library
may say which manufacturers it knows, each under the one name it goes by:

```yaml
# manufacturers.yaml
- name: Texas Instruments
  aliases: [TI, Texas Instr.]     # the other ways it gets written
- name: Yageo
- Würth Elektronik                # the shorthand: a name, no other spellings
```

- **A name is recognised however it was typed.** Case, accents, punctuation and
  the legal form at the end are not part of a name: `YAGEO Corp.`, `yageo` and
  `Yageo Co., Ltd.` are all Yageo, and `Wurth` is `Würth`, without an alias for
  each. An abbreviation is only known to be one if `aliases` says so. A word
  like "Semiconductor" *is* part of a name: `ON` and `ON Semiconductor` are
  not assumed to be the same company.
- **Everything shows the one name.** A manufacturer part whose file says `TI`
  is listed, searched, exported and placed as Texas Instruments. Searching for
  `TI` still finds it.
- **A file that says another spelling is reported.** It is recognised, and the
  file still says something else, which is what anyone reading the repository
  sees. Each such manufacturer part is on the issues page as
  `manufacturer-alias`, and a librarian can have the files rewritten (below).
- **A new manufacturer part is written down under the one name**, whichever
  way it was typed in the form, and the form offers the names the library has.
- **A name the list does not have is not refused.** The part is still a draft
  someone can work on. It is kept as typed and listed on the issues page as
  `unknown-manufacturer`, which is the worklist for whoever keeps the list.
- **A spelling can mean one manufacturer only.** One listed for two is an
  error; the first to claim it keeps it.

The list is optional. Without one nothing is asked and nothing is renamed --
but one name written more than one way is still noticed, and reported as
`manufacturer-spelling`: that is where a list starts.

**Librarians keep the list in the app**, at `/manufacturers`. The page is the
worklist first and the list second: every name a manufacturer part gives that
the list does not recognise, the most used first, each with two ways out --
*Add to the list*, or *It is this one*, which opens a listed manufacturer with
the name filled in as another way of writing it. Below that is the list itself,
with how many manufacturer parts each makes. Saving is one commit to the
repository, in the librarian's name, with an optional note, like any other
change.

**Written another way.** Between the worklist and the list, the page shows the
listed manufacturers that some manufacturer parts name by another spelling,
with the spellings and how many parts use them. *Write as Texas Instruments*
rewrites those parts' files to say the list's name; *Rewrite all* does it for
every manufacturer at once. Either is one commit in the librarian's name. Only
the `manufacturer:` line of each file is changed: its comments, its other lines
and its line endings are left as they are. (A file where that line cannot be
changed in place -- a value folded over several lines -- is written out whole,
and loses its comments.) The list itself is not changed: a spelling it has
stays in it, so the next file to use it is recognised, and reported, the same
way. On the issues page the `manufacturer-alias` group has a button that leads
here, for a librarian.

Renaming a manufacturer keeps its old name as another spelling, so the files
that say the old name still mean it -- and are then reported as written
another way, until they are rewritten. Taking a manufacturer off the list does
not touch those files either: they are back on the worklist. Writing the list
here lays the file out its own way, so a comment in a hand-written one does not
survive being edited in the app; whatever else an entry holds is kept.

### Private repositories

A source that needs a token to be read is marked `"private": true` in
`LIBRARY_SOURCES`, and `LIBRARY_READ_TOKEN` is set to a token that may read it
(on GitHub, a fine-grained token with "Contents: read-only" on that
repository). Without a read token the write token is used, which can read what
it can write.

The mark is per source because the token is for one host. A deployment may read
its own library from one host and the official KiCad libraries from another,
and a token must not go to a host it was not made for. It is sent only to the
sources marked private, only over https, and scoped to that source's host, so
that if the repository redirects somewhere else the token does not follow. It
travels in git's environment, not in the repository's address or an argument,
so it is in neither a process listing nor an error message.

The old versions of files that a part's history page reads are fetched from the
git host when asked for, with the same token. A source that wants a login and
has none says so, and says what to set.

### Indexing

The index is built in the background at startup. Cloning takes long enough
that waiting for it would fail the container health check, so the app answers
at once and reports `syncing` until the first index lands. Clones are shallow
and live under `LIBRARY_WORKDIR`, which **must be a persistent volume**.

### Keeping it current

A part pushed to the repository shows up without anyone restarting anything.
The sources are looked at again:

- **when the git host says so.** Set `WEBHOOK_SECRET`, and add a webhook for
  push events at `<PUBLIC_URL>/api/v1/library/webhook` with the same secret
  and content type `application/json`. GitHub and Gitea both sign their calls
  the way this expects. The part is in the catalog seconds after the push.
- **when a librarian asks.** "Refresh now" on the status page fetches at once
  and says what came of it.
- **on a timer**, every `LIBRARY_REFRESH_MINUTES` (15 by default, 0 to switch
  it off), for when nothing else says so.

Looking is cheap. If every source is still at the commit that was indexed,
nothing is read again and the snapshot being served is kept.

If a source cannot be reached, the previous index keeps being served and the
failure is shown on the status page.

Everything that does not line up — a footprint no source has, a symbol whose
parent is missing, an IPN in an undeclared category — is collected and listed
at `/issues`, grouped by kind, with the repository-relative path to open.

## Finding a part

The catalog's search box finds a part somebody already half knows: it looks at
the part number, the description, the field values, the manufacturer parts and
who makes them. Choosing a part that is not known yet goes the other way: a
category, and then what is wanted of it.

Once a category is chosen the catalog shows its **parameters**: what its parts
can be told apart by. Each is a name and a dropdown of the values it takes,
with how many parts each value would leave:

- **The parts' fields**: the template's fields in the template's order, then
  `Value`, then any other field a part of the category carries.
- **Footprint**, the footprint's name without its library, and
  **Manufacturer**, who makes the part's sources, each under the one name the
  [list of manufacturers](#manufacturers) gives it. Neither needs a field.

A parameter is offered only if the category's parts differ in it; one that is
the same for all of them narrows nothing.

**Counts are of what is left.** The number beside a value is how many parts
would remain if it were chosen, given everything chosen for the *other*
parameters. Choosing 0603 does not make 0402 vanish from the packages: its
count is what choosing it instead would give.

**Values are read as quantities.** A field is text, written by hand, and text
sorts 100k before 4k7. So `4k7`, `4R7`, `100nF`, `0.1 W`, `1/4W`, `1%` and
`3V3` are read as the numbers they stand for: the values in a dropdown run
from the smallest up, and `100mW` is the same amount as `0.1W`. Where every
value of a parameter is a quantity, a sign appears before its dropdown;
pressing it changes *exactly* (=) to *at least* (≥) or *at most* (≤). A
voltage rating is wanted to be enough, not to be equal.

What is read is how much, not of what: a unit is looked at only to tell a
prefix from it, which is enough because the values compared are always one
field's. A value that is not a quantity (`X7R`, `SOT-23`) can only be asked
for exactly. A bare `m` is taken for milli, so lengths written as `1m` and
`500mm` in one field will not line up.

The parameters fold away under their heading, which then says what the list is
narrowed by. Inside KiCad the panel is short, and the parts are what it is for.

Over the API the same thing is `where`, given once for each condition:

```
/api/v1/parts?category=1102&where=Resistance>=4k7&where=Resistance<=10k&where=Package=0603
```

Two conditions that say what one parameter *is* are a choice between the two;
everything else has to hold together. The answer carries `parameters`: each
parameter of the category, whether it is a quantity, and its values with their
counts. A condition that cannot be read is refused with the reason (422). The
catalog takes the same conditions in its address, so a search can be linked
to: `/panel?category=1102&where=Tolerance=1%25`.

## Requesting and approving parts

The library is authored in git, and most of the people who should be able to
add to it would rather not write YAML and push it. So the app does that part:
it writes the files, commits them in the name of whoever asked, and pushes.

- **Anyone signed in can request a part** at `/parts/new`: a category, a
  description, a symbol and footprint from the library, the fields the symbol
  should carry, and the manufacturer parts that may be bought for it. It is
  added as a **draft** under the next free number in its category, and is in
  the catalog — and placeable — at once.
- **A draft can be corrected by anyone.**
- **A librarian approves it.** From then on only a librarian changes it, and
  a librarian retires it when it should no longer be designed in.

Each of these is one commit to the repository. The author is the person; the
committer is this app, so the history shows both who wanted the change and
that it came through here. That history is the audit trail: there is no
second record of who approved what.

**Saying why.** The subject of the commit is written by the app —
`Request 1102-0002: Resistor 330R 1% 0.1W 0603`, `Approve …`, `Retire …`. Every
form has an optional note for the person's own words, which goes under the
subject as the body of the commit: why the part is needed, what was checked,
what the change is for. Approving or retiring a part asks for one first, since
that is the decision someone will want the reason for later; changing or
removing a template has one too. A note is at most 1000 characters and is kept as
written, except that characters that cannot be printed are dropped. The part's
history page shows it under the change, and so does `git log`. Commits made by
hand in git are read the same way: their message body is the note.

A manufacturer part the library already has is used as it is. A new one is
added with what the form says about it. One manufacturer part can serve
several internal parts, so a part's form never rewrites one that exists.

If somebody else pushes while a change is being saved, it is made again on top
of theirs — which is also what stops two parts requested at the same moment
from being given the same number.

This needs `LIBRARY_WRITE_TOKEN`: a token that may push to the library's
repository. On GitHub, a fine-grained personal access token limited to that
one repository with "Contents: read and write". Without it the app only reads
the library, and the pages do not offer to change it. Parts are written to the
source the parts are read from. Files this app writes are laid out its own
way; a comment in a hand-written part file does not survive an edit made here.

### Links to other sites

A datasheet, or a commit on the git host, is a link to another site. In a
browser it opens in a new tab. **Inside KiCad's panel it cannot, and that is
KiCad's doing, not a setting here.** The Remote Symbols panel keeps web links
in the panel: it loads them in the same view, and treats a request for a new
window the same way. The panel has no back button, so following a datasheet
there would leave the part page for a manufacturer's PDF with no way back. And
a page cannot ask KiCad to use the system browser instead, because the commands
KiCad accepts from a page are for signing in and for downloading and placing
parts, and none opens an address.

So inside the panel such a link does not navigate. It shows the address,
selected and copied if the WebView allows it, to paste into a browser, and the
panel stays where it was. A datasheet is shown as a small document icon; its
name is kept for screen readers and for hovering.

A change to KiCad would be needed to do better: a command that opens an address
in the system browser. That is worth asking its developers for.

### What the panel shows

Inside KiCad's panel only the catalog and the parts are shown. Managing the
library -- BOMs, products, templates, requesting or approving a part, the
issues, a part's history -- is done in a browser, and what leads to it is not
offered in the panel, which has no way back from such a page. A part's *where
used* is shown there, since it matters when choosing a part, but its projects
are named, not linked, as a BOM is a page for a browser.
`static/js/in-kicad.js`, in the head of every page, marks the page when it is
in KiCad's panel, and anything with the class `web-only` is then hidden before
it is drawn (and `kicad-only` is shown, in place of a link that cannot be followed). The pages themselves are still served to the panel if their
address is opened: this is a choice about what to show, not a barrier.

### Placing from a part's page

Inside KiCad's panel a part's page has a **Place in KiCad** button, as the
catalog's rows do; in an ordinary browser it is not shown, as there is nothing
to place into. Both send the same thing, from `static/js/place.js`: the
footprint and 3D model are saved first and the symbol is placed last. Warnings
(a footprint that could not be found, a model too large to send) are shown
before anything is sent.

KiCad opens its session once, when the panel loads the provider, and not again
for every page the panel then shows. So the page script remembers the session
in `sessionStorage` (the id, and the count of messages sent) and the pages that
follow carry on with it. Without that, going from the catalog to a part and
back left the catalog with no session to place with.

The scripts that talk to KiCad are tested against a pretend KiCad in
`tests/js/bridge_tests.js`, run from `tests/test_bridge_js.py` when Node is
installed.

### Part history

Each part's page links to its history at `/ipn/{ipn}/history`: every commit
that touched the part's file, newest first, with who made it, when, and what
changed in the part, put into words — `Status: "draft" → "approved"`,
`Tolerance: "1%" → "5%"`, `Added source ERJ-3EKF3300V` — not as a diff of YAML
lines. A commit that only reformatted the file says so. Each links to the
commit on the git host, for a GitHub or Gitea source.

There is no second record behind this. The history is the repository's, so it
covers changes made in the app and changes pushed by hand alike, under the names
git has for them. It shows the part's own file; a change to a manufacturer part
it lists is in that part's file and not shown here.

Reading old versions needs the history, so the library is cloned **with its
history but without the contents of old files**
(`git clone --filter=blob:none`). That keeps the clone about the size of the
current files, which is what matters for a repository carrying 3D models. The
contents of an old version are fetched from the git host when a history page is
opened, which is why the first view of a part with a long history can take a
moment; the result is then kept until the library moves on. A host that cannot
filter sends everything, which is slower but works.

A clone made before this existed has no history, and shows no history rather
than claiming every part was created at once. It is replaced, in full, the next
time the library is refreshed: expect one fresh clone after upgrading.

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

### Products

A company makes families of products, and each family is several devices: an
AKD servo drive in MV and HV versions of 3, 6 and 12 A is six devices, each
designed in a repository of its own. `/products` shows that as a tree, with
each device's repository linked and the BOM revisions uploaded for it. A
family is a file in the library's repository, so it is reviewed and has a
history like a part does:

```yaml
# products/akd.yaml     the file's name is the family's id
name: AKD Servodrive
description: Digital servo drives
repository: https://github.com/acme/akd       # optional: what the whole family shares
tags: [servo, drive]                          # optional
devices:
  - id: mv-3                                  # unique within the family
    name: AKD MV 3
    repository: https://github.com/acme/akd-mv-3
    tags: [mv, 3A]
  - id: hv-6
    name: AKD HV 6
    tags: [hv, 6A]
```

A device is named by its family and id, `akd/mv-3`. The same id may be used in
another family. Devices are independent: each has its own BOM, and nothing is
shared between the MV and HV versions of a drive.

**Attaching BOMs.** Upload form: choose the device, and the project is filled
in with its name unless something else was typed. A BOM already stored is
attached, or moved, on its own page. Anyone who may upload may do this; the
BOMs are not changed by it. A BOM on no device, or on one that has since been
taken out of the files, is listed under *Not on a device*, not lost.

**Searching.** The page has a search box and a tag filter. A family's tags
count for each of its devices, tags that differ only in case are one tag, and
the search looks at names, descriptions, tags and BOM revisions.

A repository that is not a web address is shown as text and reported on the
issues page, and is not linked. Mistakes in a file are issues like any other
in the library; the other families still load.

Defining products is, for now, editing these files; there is no form for it
yet.

### Where BOMs are kept

The library is in git because it is authored and reviewed. BOMs are records of
what was built and are looked up by part, so they are in a database: one
SQLite file at `DATABASE_PATH`, by default `/data/library-manager.db`, next to
the library's clones.

**That file is the one thing here that cannot be rebuilt from somewhere else.**
The clones can be cloned again; the BOMs cannot be uploaded again by anyone who
no longer has them. The original upload is stored whole beside the lines read
from it. The signed-in sessions are kept in the same file, so that a restart
does not sign everyone out; only a hash of each session's cookie is stored, so
a copy of the file holds nothing that could be put in a browser.

The file is created on first use. The directory it is in is not: a path that
points somewhere that does not exist is reported on the status page instead,
and sessions then carry on in memory alone, as they did before there was a
database.

Its tables are made by numbered steps, in `app/db/migrations.py`, and the file
remembers how many it has been through. A new version of the app that needs a
new table or column adds a step, and the step is taken once, when that version
first opens the file; nothing has to be run by hand. A database from before
the steps were numbered is taken as it is and brought up to date the same way.
A file that a *newer* version has written to is left alone and reported: after
going back a version, restore a backup from before the upgrade, or go forward
again.

### Backups

The app copies the database **once a day** to `backups/` beside it
(`BACKUP_DIRECTORY` to put them elsewhere), keeping the last `BACKUP_KEEP`
(14; 0 switches them off). A copy is made with SQLite's own backup, which is
consistent while the app is writing, and leaves out the sessions. The first
one is made when the app starts if none is less than a day old, so a
deployment that is restarted often still has one. The status page shows when
the last one was made, and calls it out as overdue after three days.

Those copies are on the same disk as the database. They protect against a bad
deploy or an upload deleted by mistake, **not against losing the volume**. So a
librarian can also **download a copy** — from the status page or the BOMs page,
or `GET /api/v1/backup` — to keep somewhere that is not this server. Doing that
now and then is worth it; if the host offers volume backups, switch those on
too.

To restore, stop the app, replace the database file with a backup (or a
download), and start it again. Sessions are not in a copy, so everyone signs in
once more.

## Suppliers

The library says which manufacturer parts may be bought for a part. Whether
one *can* be, today, and what it costs, is a supplier's to say, and changes
daily. So a part's page asks: under *Supply* it shows, for each manufacturer
part and each supplier, what is in stock, the supplier's status for it, the
price of one piece when buying 1, 10, 100 and 1000, and the manufacturer's
lead time, with a link to the supplier's page.

What a supplier says is shown beside what the library says and never written
into it. Where the two disagree for the worse -- the library has a part as
active and the supplier as obsolete, or not for new designs -- the supplier's
status is marked. What follows from that is a person's to decide.

### Asking, and not asking twice

A supplier allows only so many calls a day, so a call is not made lightly:

- **The page asks once it is on screen**, not while it is being made. A
  supplier takes its time to answer, and the page does not wait on it.
- **An answer is kept**, in the database, under a hash of which supplier was
  asked and what. The same thing is not asked again while the answer holds:
  `SUPPLIER_CACHE_HOURS`, a day by default. That a supplier does not list a
  part is an answer too, and is kept the same way. A supplier is therefore
  called once a day for each manufacturer part that somebody looks at, however
  often it is looked at; parts nobody opens cost nothing.
- **Every call is counted**, per supplier and day, against a limit
  (`DIGIKEY_DAILY_LIMIT`). When the day's calls are used up nothing more is
  asked until the next day (in UTC, as suppliers count). An answer that no
  longer holds is then shown anyway, with the day it is from: old is better
  than nothing.
- **A supplier that fails is left alone for ten minutes**, and what is kept is
  shown meanwhile.

The status page shows, for each supplier, how many calls have gone to it today
out of how many, how many answers are kept, and when it was last asked -- and,
where it last did not answer, why, in the supplier's own words: keys that are
not its keys, an application not subscribed to the API. A part's page says
only that it did not answer; the reason is for whoever can do something
about it. What
is kept is the answer as the supplier gave it, and it is read again each time
it is shown, so that a better reader can be put to answers already paid for.
Copies of the database are made without these answers: they can be had again
by asking.

### DigiKey

Register an application at [developer.digikey.com](https://developer.digikey.com),
subscribe it to **Product Information v4**, and give this app its client id
and secret:

| Variable | |
| --- | --- |
| `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET` | The application's keys. Without both, DigiKey is asked nothing. Set them in the deployment's environment; they do not belong in the repository. |
| `DIGIKEY_LOCALE_SITE`, `DIGIKEY_LOCALE_LANGUAGE`, `DIGIKEY_LOCALE_CURRENCY` | Whose prices and stock: `US`, `en`, `USD` unless said otherwise. For Germany: `DE`, `en` or `de`, `EUR`. |
| `DIGIKEY_DAILY_LIMIT` | How many searches a day may be sent. 1000, which is what DigiKey allows a registered application; this app stops itself before DigiKey has to. |
| `DIGIKEY_API_URL` | `https://sandbox-api.digikey.com` answers the same way with made-up data, for trying the keys out. |
| `SUPPLIER_CACHE_HOURS` | How long an answer holds before the same thing is asked again. 24. |

A part is found by searching for its manufacturer's part number
(`POST /products/v4/search/keyword`). Only a product that goes by exactly
that number is taken for it; where one number is two manufacturers' products,
the one from the part's manufacturer is meant, which is where the
[list of manufacturers](#manufacturers) earns its keep: DigiKey has its own
name for each. Prices are those of the packaging with the smallest order, from
DigiKey's own stock rather than a marketplace seller's.

### Another supplier

A supplier is one class in `app/suppliers/` that does two things: *fetch*,
which sends one question and returns what came back, and *read*, which says
what such an answer means as an offer. Whether to ask at all -- the kept
answers, the count, the limit -- is `app/services/supply.py`, and is the same
for every supplier. Adding one is that class, and a line in `app/application.py`
that builds it from the settings.

## Configuration

Everything is set through environment variables; [.env.example](.env.example)
lists them all with their reasons. The ones a deployment needs:

| Variable | |
| --- | --- |
| `PUBLIC_URL` | The externally reachable address, including any path prefix. Every URL the app hands out is derived from it. |
| `LIBRARY_SOURCES` | JSON array of `{id, name, url, ref, private}` git repositories. Add `"private": true` to one that needs a token to be read. |
| `LIBRARY_READ_TOKEN` | A token that may read the sources marked private. The write token is used if this is not set. |
| `BACKUP_KEEP`, `BACKUP_DIRECTORY` | How many daily copies of the database to keep (14; 0 for none), and where. |
| `LIBRARY_WORKDIR` | Where the clones are kept. A volume. |
| `DATABASE_PATH` | The SQLite file that holds the BOMs. On the same volume, and the thing to back up. |
| `AUTH_ENABLED`, `OIDC_METADATA_URL`, `OIDC_CLIENT_ID` | Sign-in; see below. |
| `LIBRARIANS` | Who may refresh the library and delete BOMs: comma-separated usernames, e-mail addresses or subject ids. |
| `LIBRARY_WRITE_TOKEN` | A token that may push to the library's repository. Lets parts be requested and approved in the app. |
| `WEBHOOK_SECRET` | Shared with the git host's webhook, so a push refreshes the library at once. |
| `LIBRARY_REFRESH_MINUTES` | How often to look for new commits unprompted. Default 15; 0 for never. |
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

### Librarians

Anyone signed in can read the library, upload a BOM, and request a part or
correct a draft. What decides something, or throws a record away, is a
librarian's to do: approving a part, changing or retiring an approved one,
editing a category's template, refreshing the index, deleting a BOM.

Librarians are listed in `LIBRARIANS`, by the username, e-mail address or
subject id the identity provider reports for them — not by display name, which
is what a person chooses to be called. They are named here rather than by a
group claim from the provider because of the token limit described below: a
claim would ride in two of KiCad's three tokens, and a list costs nothing.

If your provider lets people change their own username, list e-mail addresses
or subject ids instead.

With `AUTH_ENABLED=false` there is nobody to tell apart, so everyone can do
what a librarian can.

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
- Run one instance. Sessions are kept in the database file, so a restart does
  not sign anyone out, but two instances would not share what each has in
  memory, and the library's index and the BOM store are not built for it.

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
| `/api/v1/parts?q=&status=&category=&where=&limit=` | Part search. `where` narrows by what a part says about itself (`Resistance>=4k7`), and within a category the answer says what is left to choose | |
| `/api/v1/parts/{ipn}` | One part, with its sources | |
| `/api/v1/parts/{ipn}/assets` | What KiCad is sent to place it | |
| `/api/v1/parts/{ipn}/symbol.svg`, `/footprint.svg`, `/model.step` | Drawings and the 3D model | |
| `/api/v1/issues?severity=&limit=` | Everything that does not line up | |
| `POST /api/v1/parts` | Request a part: added as a draft under the next number | |
| `PUT /api/v1/parts/{ipn}` | Change a part; a draft by anyone, otherwise librarians | |
| `POST /api/v1/parts/{ipn}/status` | Approve, retire or return to draft; librarians | |
| `/api/v1/library/choices` | The categories, symbols, footprints and manufacturer parts a form offers | |
| `/parts/new`, `/ipn/{ipn}/edit` | The forms for the above | |
| `PUT`, `DELETE /api/v1/templates/{category}` | Create or replace a category's template, or remove it; librarians | |
| `/templates`, `/templates/{category}/edit` | Listing and editing templates; librarians | |
| `/api/v1/manufacturers` | `GET` the manufacturers and the names not on the list; `PUT {was, name, aliases, note}` adds one or changes the one called `was`, `DELETE {name, note}` removes one (librarians) | |
| `POST /api/v1/manufacturers/rewrite` | `{name, note}`: rewrite the manufacturer parts that name a listed manufacturer by another spelling so that their files say the list's name; without `name`, for every manufacturer (librarians) | |
| `/manufacturers`, `/manufacturers/new`, `/manufacturers/edit?name=` | The worklist and the list; adding and editing a manufacturer; librarians | |
| `POST /api/v1/library/refresh` | Fetch the sources now and index what changed; librarians | |
| `POST /api/v1/library/webhook` | The same, for the git host; let in by its signature | ✓ |
| `/api/v1/boms` | `GET` the stored BOMs; `POST` one as JSON `{project, revision, note, filename, content, device, replace}` | |
| `/api/v1/boms/{id}` | `GET` a BOM against the library; `DELETE` it (librarians) | |
| `/api/v1/boms/{id}/export.csv`, `/source.csv` | The BOM with library columns added; the file as uploaded | |
| `PUT /api/v1/boms/{id}/device` | Say which device a stored BOM is the BOM of: `{device: "akd/mv-3"}`, or `""` for none | |
| `/api/v1/products` | The product tree, each device with its BOMs, and those on none | |
| `/api/v1/parts/{ipn}/where-used` | The stored BOMs a part is on | |
| `/api/v1/parts/{ipn}/supply` | What each supplier says about the part's manufacturer parts: stock, status, prices | |
| `/ipn/{ipn}` | The part's page; where `Datasheet` points | |
| `/ipn/{ipn}/history`, `/api/v1/parts/{ipn}/history` | What was changed in the part, by whom, and when | |
| `/boms`, `/boms/{id}` | Uploading BOMs; one BOM against the library | |
| `/products` | Families, devices, their repositories and BOMs; searchable | |
| `/issues` | The issue list | |
| `/docs` | Interactive API reference: every JSON answer, field by field | ✓ |

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

```bash
mypy
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
  errors.py          the kinds of thing that can go wrong, for every layer to raise
  web/               HTTP: routes, pages, request-scoped dependencies, errors as answers
  services/          what can be done: one function for each action, no HTTP
  suppliers/         the suppliers that can be asked about a manufacturer part
  auth/              the OIDC provider, sessions, safe redirects
  boms/              reading KiCad BOMs, keeping them, setting them against the library
  db/                the database: how it is opened, its tables as numbered steps, backups
  library/           sources, assets, parts, snapshots, placement, previews
  domain/            parts, templates, what is asked of them, who may change them
  kicad/             the file formats: reading, rewriting, drawing
  templates/  static/
tests/
  support.py         library files, a library on disk, a fake identity provider
```

`kicad/` knows nothing about parts or HTTP; `library/` and `boms/` know nothing
about HTTP. `domain/` does no I/O at all: it is given what it needs and says
what it found. What a part has to say about itself is decided there once, in
`domain/rules.py`, for a part being requested and for one already in the
library alike; who may change a part is decided in `domain/policy.py`, for
the routes and the pages alike.

Each thing that can be done -- requesting a part, approving it, changing a
template, uploading a BOM -- is one function in `services/`: who may do it,
whether what was asked makes sense, the change, and what came of it. A route
turns a request into that call and the outcome into an answer, and nothing
more. What goes wrong is raised as one of the kinds in `errors.py` (not found,
not allowed, invalid, conflict, unavailable, upstream failed) and becomes a
status code in exactly one place, `web/errors.py`: a route does not catch an
error in order to answer it.

The pages' scripts, in `static/js/`, are ES modules, served as they are
written: there is no build step and nothing to install. Each imports what it
uses -- `app.js` for talking to the API and building DOM, `place.js` for
placing a part -- so a page names only its own script and the order of the
tags decides nothing. Two scripts are *not* modules, on purpose:
`in-kicad.js` and `kicad-bridge.js` are ordinary scripts in the page's head,
because each has to run before the page is drawn or before KiCad speaks, and
a module never runs that early. `bridge.js` is how the modules reach what the
bridge leaves on the window.

What each JSON endpoint takes and answers is written down in `web/schemas/`,
as models with named, typed fields; a route returns one, built from the app's
own objects by the model's `of` method. That is the contract the panel's
scripts are written against and what `/docs` shows. A test holds every
documented endpoint to naming its answer, so a new one cannot be added
without saying what it sends.

In `library/`, `files.py` is the one module that knows where each kind of
library file is and how it is written down: the loaders read through it and
the editors write through it, so what the app writes is what it can read back.
`service.py` holds the snapshot being served and the lock that keeps a refresh
and a change apart, and leaves the work to `indexer.py` (fetch the sources,
read them into a snapshot), `writer.py` (commit and push, again if somebody
pushed first) and the history cache in `history.py`.

Whatever is kept in the database has a store of its own beside the code that
uses it (`boms/store.py`, `auth/sessions.py`). None of them opens the file:
each is handed the one `Database` from `db/`, which is also where every table
is made. Keeping something new means a step in `db/migrations.py` and a store
that takes a `Database`. The app is built by a factory from a `Settings` object, so a test builds
the deployment it wants instead of patching the environment.

The reader for KiCad's files is deliberately not a model of the format: a file
is a generic tree, so a token a newer KiCad adds is just another node. The
writer reproduces KiCad's own layout, so a served file is indistinguishable
from one KiCad saved.

The discovery document is validated against KiCad's own JSON schema, a verbatim
copy of which is in `tests/fixtures/`. Refresh it when targeting a newer KiCad.
