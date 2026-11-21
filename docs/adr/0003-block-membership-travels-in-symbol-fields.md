---
status: accepted
---

# A placed block is recognised by fields written into its symbols

KiCad records nothing that survives into a BOM about which design block, or
which revision of it, a part was placed with: a group placed from a block
keeps only `<Library>:<Block>`, and a BOM knows nothing of groups. So the app
writes it into the block's own symbols when it accepts a revision: a marker
symbol (reference `DB`, on the BOM, not on the board, DNP) whose Datasheet is
the revision's page, as a part's Datasheet is the part's page, and a `Block`
field, `DB-0012 B`, on every component. Uploaded BOMs then say where each
revision is used and whether a placed block was edited, with no new kind of
upload.

This is hard to undo: every board placed with a block carries these fields
for good. If KiCad comes to record the block and revision itself (ideas 3, 4
and 6 in [What we would ask KiCad for](../kicad-feature-ideas.md)), the app
should read that as well, not instead.

## Considered options

- **Detect blocks from uploaded schematics** rather than BOMs. Exact, but a
  new upload for designers, and a schematic still does not say which revision.
- **Declare on each device which revisions it uses.** Nothing written into
  the block, but it depends on someone remembering, and cannot notice an
  edited block.
- **Put the revision in the block's name.** Readable from KiCad's group link,
  but then KiCad lists every revision as a block of its own (see ADR 0001).

## Consequences

- The marker must be on the BOM to be seen, so costing and supply skip it by
  its Datasheet address. Its reference must not start with `#`, which KiCad
  treats as a power symbol and leaves out.
- The edit check needs a `Block` column in the BOM export. Without one, the
  markers still give where used.
- What KiCad does with these fields on placing and on *Update Symbols from
  Library* has been read from its source, not tried; see the spec.
