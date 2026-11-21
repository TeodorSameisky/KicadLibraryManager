# Design blocks

Status: designed 2026-11-21, not built. The words are defined in
[CONTEXT.md](../../CONTEXT.md); the decisions that are hard to undo are
[ADR 0001](../adr/0001-block-revisions-are-git-tags.md),
[0002](../adr/0002-drafts-live-on-their-own-branches.md) and
[0003](../adr/0003-block-membership-travels-in-symbol-fields.md).

A company reuses tested circuits across its products: a buck converter, a
USB-C input, a CAN transceiver. A design block is one of them, kept in the
library with its documentation and the requirements it meets, made of the
library's parts, and checked against them for as long as any board carries
it. When a part in it is retired or can no longer be bought, its owner hears
of it and makes a new revision.

- [What it is made of](#what-it-is-made-of)
- [Revisions](#revisions)
- [The marker and the Block field](#the-marker-and-the-block-field)
- [Getting a block into the library](#getting-a-block-into-the-library)
- [Getting it into KiCad](#getting-it-into-kicad)
- [What is checked](#what-is-checked)
- [Where used, and edited blocks](#where-used-and-edited-blocks)
- [The block's page](#the-blocks-page)
- [Settings](#settings)
- [Not now](#not-now)
- [To try in KiCad first](#to-try-in-kicad-first)
- [Order of work](#order-of-work)

## What it is made of

KiCad's own design block files, as its editor saves them, with what KiCad has
no place for in a YAML file beside them:

```
block-libraries.yaml
blocks/<Library>.kicad_blocks/<Name>.kicad_block/
    <Name>.kicad_sch        the circuit, as KiCad saves it
    <Name>.json             description, keywords, fields: KiCad's
    block.yaml              what KiCad does not hold
    README.md, images       the documentation, if kept here
design-block-lib-table      at the top, beside sym-lib-table
```

Schematic only for now. A KiCad 10 layout is a `<Name>.kicad_pcb` beside the
schematic and can be added later without changing anything else.

**`block-libraries.yaml`**, kept by librarians as `categories.yaml` is: the
libraries a block may be filed under.

```yaml
- name: Power
  description: Regulators, protection, power inputs
- name: Interfaces
```

**`block.yaml`**:

```yaml
number: DB-0012
revision: B
status: released            # draft | released | retired
owner: jdoe
documentation: https://git.example.com/hw-docs/usb-c-input
documentation_ref: 3f2a91c  # the documentation's commit or tag for this revision
requirements: [REQ-1201, REQ-1207, https://other.example.com/req/88]
```

| Key | | |
| --- | --- | --- |
| `number` | required | `DB-<number>`, given out like an IPN: the next after the highest. Never changes. |
| `revision` | required | A letter, `A` first. Given when a draft is started. |
| `status` | | `draft` on a draft's branch; `released` on the library's branch; `retired` when no new design should use the block. *Superseded* is not stored: every released revision but the newest is. |
| `owner` | | Who to ask about the circuit. Set by a librarian. |
| `documentation` | | Where the documentation lives, if not (only) in the block's directory. Per block. |
| `documentation_ref` | | Which commit or tag of it goes with this revision. Required on release when `documentation` is given. |
| `requirements` | | What this revision meets: ids, made links by `REQUIREMENTS_LINK`, or full addresses. Copied into a new draft. |

The block's name is its directory's name without the suffix, and may not
contain a dot: KiCad cuts a name at the first one.

## Revisions

One directory per block; git is its history ([ADR 0001](../adr/0001-block-revisions-are-git-tags.md)).

| | Where | Who changes it |
| --- | --- | --- |
| **Draft** `C` | branch `drafts/DB-0012-C` | any designer |
| **Released** `B`, the current revision | the library's branch, and tag `blocks/DB-0012/B` | nobody: a change is a new revision |
| **Superseded** `A` | tag `blocks/DB-0012/A` only | nobody |
| **Retired** | the library's branch, `status: retired` | a librarian may reinstate it |

- **Starting a draft** branches from the library's branch, takes the next
  letter, and copies the requirements and documentation link forward. An
  abandoned draft leaves a gap in the letters.
- **Releasing** is a librarian's, with a note, as approving a part is. It is
  refused while the draft has any part warning or a part that is not approved,
  or lacks `documentation_ref` where `documentation` is given. It merges the
  draft and pushes the tag.
- **Retiring** a block: no revision of it should go into new designs. Boards
  carrying it go on being checked.

## The marker and the Block field

[ADR 0003](../adr/0003-block-membership-travels-in-symbol-fields.md) says why.
Every revision's schematic carries:

- **The marker**: the library symbol `Blocks:DesignBlock`, reference `DB1`, on
  the BOM, not on the board, DNP. Its fields:
  - `Block`: `DB-0012`
  - `Revision`: `B`
  - `Value`: `USB-C Input rev B`, so it reads as a label on the schematic
  - `Datasheet`: the revision's page, `https://library.example.com/blocks/DB-0012/B`
- **The Block field** on every other symbol in the block: `Block: DB-0012 B`.

The `.json`'s `fields` carry `Block` and `Revision` too, so a block placed as
a sheet has them as sheet fields.

The app writes all of these, on every upload and release, adding the marker
if the upload has none. Nobody writes them by hand.

## Getting a block into the library

A designer draws the circuit in KiCad, using the library's parts, and saves
it as a design block in a library of their own. Then, in the app:

- **New block**: upload the block's folder, zipped, or just its `.kicad_sch`,
  which the app wraps in a folder. Choose its library and give a
  description and keywords; the app gives the number and starts draft `A`.
- **New revision** of an existing block: the same, onto a new draft.
- **Correcting a draft**: upload again onto the same draft, or edit its
  description, documentation and requirements.

On every upload the app:

1. checks that it is a KiCad 9 or later schematic, and that the name has no dot;
2. rewrites every `lib_id` and `Footprint` to the repository's own library
   names: a block drawn through the Remote Symbols panel names
   `remote_Passives:R_0603`, which exists on its designer's computer only
   (`BLOCK_LIBRARY_NAMES`);
3. writes the marker and the Block field;
4. writes the `.json` from the form;
5. commits to the draft's branch in the designer's name.

## Getting it into KiCad

Designers clone the library's repository, keep it pulled, and in KiCad add:

- the repository's `design-block-lib-table` entries to their own design block
  libraries, under `${KICAD_LIBRARY_DIR}` (the variable the HTTP library
  already uses: KiCad has none of its own for design blocks);
- its symbol and footprint libraries, as for the HTTP library, so the block's
  footprints are found on *Update PCB from Schematic*.

Then place blocks from KiCad's own design block panel, with **Place as
group** (KiCad 10), so the group keeps its link to the block and its layout
can be applied once there is one. The clone only ever has released revisions.

Neither the Remote Symbols panel nor the HTTP library can carry a design
block; KiCad has no protocol for it. The block's page offers its revisions as
a zip for anyone without a clone.

The repository's `design-block-lib-table` is checked as `sym-lib-table` is: a
block library not in it is reported.

## What is checked

Each time the library is read, the current revision and every revision found
on a stored BOM (read at its tag), as for parts. Listed on the issues page,
which can be filtered to one owner's blocks.

| | | |
| --- | --- | --- |
| A component that is not a part of the library | warning | Exempt: power symbols and the marker. Place it from the library, or ask for the part. |
| A part that is retired | error | Make a new revision. |
| A part with nothing that can be bought: every manufacturer part `nrnd` or `obsolete` | error | Make a new revision. |
| A part whose preferred manufacturer part is `nrnd` | warning | Think about a new revision. |
| No marker, a marker that does not match `block.yaml`, a dot in the name | error | Upload again through the app. |
| A block library not in `block-libraries.yaml` or `design-block-lib-table` | error, warning | |

The kinds' names are settled when built, after the existing ones
(`missing-symbol`, `obsolete-mpn`).

Later, with SiliconExpert data: *years to end of life below a threshold*, as a
warning. Whether the threshold is one for the company or one per product is
decided then.

## Where used, and edited blocks

A stored BOM is read as now. A line whose Datasheet is a revision's page is a
marker: it is counted as a placed block, and costing and supply skip it.

- **Where used**: each revision's page lists the devices whose BOMs carry its
  marker, and how many times.
- **Edited blocks**: where the BOM was exported with a `Block` column, the
  parts carrying `DB-0012 B` should be the revision's parts, times the number
  of markers. If not, the BOM's page warns, naming what is missing and what
  is extra. It cannot say which placed block was edited, and says so. Without
  a `Block` column the page says the check needs one.

A placed block is a black box: the designers' guide says not to edit one, and
to start a new revision instead. KiCad cannot prevent an edit; this check is
how one is noticed.

## The block's page

For each revision, newest first:

- the schematic, drawn as symbols are now;
- its parts, with their status and what is at risk;
- what it costs at a chosen number of boards, from the costing BOMs have;
- the documentation: the block's `README.md` rendered, a link to the
  external documentation at its `documentation_ref` (an exact link for
  GitHub and GitLab, the address and ref side by side otherwise), or both;
- the requirements, as links;
- where used;
- the owner;
- download as a zip; *History* from git.

The catalog gets a *Blocks* view, listed by library, searched by name,
description and keywords.

## Settings

| | | |
| --- | --- | --- |
| `BLOCK_LIBRARY_NAMES` | `repository` (default) or `as-drawn` | Whether uploads have their library names rewritten to the repository's. |
| `REQUIREMENTS_LINK` | e.g. `https://req.example.com/workitem?id={id}` | Turns a requirement id into a link. Without it, ids are shown as text. Not tied to any one tool. |

## Not now

- Layouts (`.kicad_pcb` in the block). The format leaves room.
- E-mail or other notifications. The issues page, filtered by owner, is how an
  owner hears; making that page a proper dashboard is a feature of its own.
- Reading requirements from the requirements tool. Writing back to it: never.
- Saying which placed block was edited.
- The end-of-life threshold.

Things KiCad could do that would replace some of this are in
[What we would ask KiCad for](../kicad-feature-ideas.md).

## To try in KiCad first

Read from KiCad's source, not tried. Each needs trying in KiCad 10 before the
part of the work that relies on it:

1. Placing a block, as a group and as a sheet, keeps its symbols' fields
   (`IPN`, `Block`, `Datasheet`) as saved.
2. *Update Symbols from Library* leaves the `Block` field alone.
3. A marker that is on the BOM, not on the board and DNP appears in the BOM
   export with its Datasheet, and nowhere on the PCB.
4. With a `Block` column, KiCad keeps a block's parts on BOM lines of their own.
5. KiCad ignores `block.yaml`, `README.md` and images in a `.kicad_block`
   directory.
6. A block whose symbols name the repository's libraries places and updates
   the PCB for a designer who added those libraries.

## Order of work

Each step is usable on its own.

1. **Try the six things above in KiCad.** What fails changes the marker or the
   layout of the files.
2. **Read blocks**: index `blocks/`, `block-libraries.yaml` and
   `design-block-lib-table`; the catalog's *Blocks* view and the block's page;
   the checks on the current revision. Blocks written by hand to start with.
3. **Read released revisions from their tags**, and check every one in use.
4. **Drafts and release**: branches, upload with the rewriting, the marker
   and the Block field, release with merge and tag, retire.
5. **BOMs**: recognise markers, skip them in costing, where used, edited
   blocks.
6. **The rest of the page**: documentation, requirements, owner, cost, zip.
