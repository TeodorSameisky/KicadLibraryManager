# What we would ask KiCad for

Things KiCad does not do that the app works around, kept here until they are
worth an issue on [KiCad's tracker](https://gitlab.com/kicad/code/kicad/-/issues).
Each says what is missing, what we would ask for, and what it would let the app
drop. Add to it whenever a workaround is made for something KiCad could do
itself; move an idea to *Filed* with the issue's link once it is one.

What KiCad does was read from its source on the 10.0 branch in November 2026,
not tried in KiCad, unless it says otherwise.

- [Design blocks](#design-blocks)
- [Watching](#watching)
- [Filed](#filed)

## Design blocks

These came out of designing the library's design blocks: tested circuits,
kept in the library repository, placed from KiCad's own design block panel.

### 1. Design blocks through the Remote Symbols panel

**Missing.** The remote provider protocol carries symbols, footprints, SPICE
models and 3D models only: no command, content type or `asset_type` for a
design block (`resources/schemas/kicad-symbol-server-rpc-v1-combined.schema.json`,
`kicad-remote-symbol-manifest-v1.schema.json`). A designer who gets parts
through the panel, with nothing checked out, still needs a clone of the
library for its blocks.

**Ask.** A design block asset type, placed the way PLACE_COMPONENT places a
symbol, with the block's footprints and models sent alongside it.

**Lets us drop.** The clone for designers on KiCad 10, and rewriting a block's
`remote_<library>` names to the repository's, which is only needed because a
block drawn by a panel user names libraries that exist on that user's
computer alone.

### 2. A design block library served over the network

**Missing.** `design-block-lib-table` accepts a folder (`KiCad`) or a nested
table, nothing else (`common/design_block_io.cpp`). There is no HTTP or
database design block library, as there is for symbols.

**Ask.** A design block library type read from a server, as the HTTP library
is for symbols.

**Lets us drop.** The clone, for KiCad 8 and 9 users as well.

### 3. A revision in a placed block's library link

**Missing.** A block placed with *Place as group* records `<Library>:<Block>`
on the group and nothing else (`eeschema/tools/sch_drawing_tools.cpp`,
`SetDesignBlockLibId`). Which revision was placed is lost.

**Ask.** The block's revision, or a digest of its content, stored with the
link, and a way to see that the library's copy has moved on (as symbols show
a changed library symbol).

**Lets us drop.** The marker symbol that carries the block number and
revision into the schematic and the BOM.

### 4. The block's fields on the group

**Missing.** The `fields` in a block's `.json` become sheet fields when the
block is placed as a sheet, and are dropped when it is placed as a group.

**Ask.** The same fields on the group, shown in Group Properties and
available as text variables.

**Lets us drop.** Part of the marker symbol: its `Block` and `Revision` would
be the group's.

### 5. Placed blocks that cannot be edited by accident

**Missing.** Nothing stops a designer changing a part inside a placed block.
A block meant as a black box drifts from its revision without anyone
deciding it should.

**Ask.** A placed design block group that is locked: its contents can be
moved as a whole but not edited, until the designer *detaches* it, which
drops the library link on purpose.

**Lets us drop.** The count check on BOMs, which guesses at edits after the
fact, and cannot say which instance was edited.

### 6. Which block instance a part belongs to, in the BOM

**Missing.** A BOM export knows nothing of groups. A part inside a placed
block cannot be told from the same part beside it, and two instances of one
block cannot be told apart.

**Ask.** A BOM column (or text variable) for the design block a symbol was
placed with, and one for the instance: the group's name or id.

**Lets us drop.** The `Block` field the app writes into every component of a
block, and the limit that edits are reported per block, not per instance.

### 7. Block names with a dot are cut short (bug)

**Missing.** A block's name is taken from its directory name up to the
*first* dot (`dirname.Before('.')` in `common/design_block_io.cpp`), so
`LDO_3.3V.kicad_block` is listed as `LDO_3`.

**Ask.** Take the name up to the last dot, as the suffix is.

**Lets us drop.** Refusing dots in block names.

### 8. A path variable for design blocks

**Missing.** There are `KICADn_SYMBOL_DIR`, `_FOOTPRINT_DIR` and
`_3DMODEL_DIR`, but no `KICADn_DESIGN_BLOCK_DIR` (`common/env_vars.cpp`). A
library's `design-block-lib-table` has to use a variable of its own.

**Ask.** A predefined one, set alongside the others.

**Lets us drop.** Little; a small consistency.

## Watching

### Managed Document Workspace

[Merge request !2598](https://gitlab.com/kicad/code/kicad/-/merge_requests/2598),
open, aimed at KiCad 11, last active 2026-11-20. It has no description yet;
what follows is from its commit messages.

Team libraries kept in a git remote, with lifecycle states on parts, releases
as git tags, a released symbol or footprint revised rather than edited, an
`MDW_Part` field linking placed symbols to their part, and a comparison of a
design's BOM with the workspace. Its backends are pluggable; the only one
named works over plain git.

It overlaps much of what this app does for parts, and the revisions-as-tags
model is the one chosen here for design blocks. Design blocks are not
mentioned in it. Things to find out once it settles:

- whether design blocks become managed documents, with revisions: that would
  answer 3, 5 and part of 1;
- whether the backend interface is public, so this app's library could be a
  backend rather than a parallel to one;
- whether `MDW_Part` would replace the `IPN` field symbols carry from here.

## Filed

None yet.
