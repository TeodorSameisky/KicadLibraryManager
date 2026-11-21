# KiCad Library Manager

A company's KiCad library kept as a git repository: the parts designers may
place, what can be bought for them, and the tested circuits they reuse across
products. The app serves it to KiCad and lets designers and librarians keep it
without working in git.

## Language

### People

**Designer**:
Someone who designs boards with the library. May ask for parts and blocks and
correct drafts.
_Avoid_: User, engineer

**Librarian**:
Someone who decides what goes into the library: approves parts, releases
block revisions, retires either.
_Avoid_: Admin, maintainer

**Owner**:
The designer answerable for one design block: the person to ask about the
circuit, and who makes its next revision.
_Avoid_: Maintainer, author

### Parts

**Part**:
An internal part, named by its IPN: what a designer places. It names a symbol
and a footprint and says what the part is.
_Avoid_: Component, item

**IPN**:
The internal part number, `1102-0001`: a category code and a number.
_Avoid_: Part number, PN

**Manufacturer part**:
Something that can be bought for a part, named by its MPN. One part usually
allows several; one manufacturer part may serve several parts.
_Avoid_: MPN (for the thing rather than its number), vendor part

**Approved**:
A part's status once a librarian has checked it: it may be designed in.
_Avoid_: Released (that is for block revisions)

**Retired**:
No longer for new designs, but kept so that the boards using it still read.
Said of a part (status `obsolete`) and of a design block.
_Avoid_: Deleted, deprecated

### Products

**Product family**:
A group of related products, with the devices in it.

**Device**:
One product in a family, `akd/mv-3`, to which BOMs are attached.
_Avoid_: Board, project

### Design blocks

**Design block**:
A tested circuit, made of parts from the library, that designers place
whole and treat as a black box. It lives through a series of revisions.
_Avoid_: Block (alone, when it could mean a KiCad group), snippet, module,
reference design

**Block number**:
A design block's identity, `DB-0012`, given out like an IPN. It never
changes; the block's name may.
_Avoid_: Block id, block name (for the identity)

**Block library**:
One of the fixed groups design blocks are filed under, `Power`,
`Interfaces`, as KiCad's design block panel lists them.
_Avoid_: Block category

**Revision**:
One state of a design block, named by a letter, `DB-0012 B`, together with
its documentation and the requirements it meets.
_Avoid_: Version

**Draft**:
A revision being worked on. Designers may change it; KiCad users do not see it.
_Avoid_: Work in progress, pending revision

**Release**:
A librarian's act of freezing a draft into a revision that may be placed.
After release a revision never changes; changing it means a new revision.
_Avoid_: Approve (that is for parts), publish

**Released revision**:
A revision that has been released. The newest is the **current revision**;
older ones are **superseded** but still count wherever they are placed.
_Avoid_: Approved revision, old version

**Marker**:
The one symbol in every design block that names its block number and
revision, so that a schematic and its BOM say which revision was placed.
_Avoid_: Tag symbol, block symbol, title block

**Block field**:
The field every component of a design block carries, `DB-0012 B`, saying
which revision it came in with.
_Avoid_: Group field

**Placed block**:
One instance of a revision on a board. A board may carry the same revision
several times.
_Avoid_: Block instance, copy

**Where used**:
The devices whose BOMs carry a revision's marker.

**At risk**:
Said of a revision when a part in it can no longer be relied on: retired,
not buyable, or nearing end of life. The cue for a new revision.
_Avoid_: Obsolete (that is a status of parts), broken

**Edited block**:
A placed block whose parts no longer match its revision: the black box was
opened on the board.
_Avoid_: Modified block, diverged block
