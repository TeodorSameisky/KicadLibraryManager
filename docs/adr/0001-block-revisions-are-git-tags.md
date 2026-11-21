---
status: accepted
---

# Block revisions are git tags on one directory

Each design block is one directory in the library repository, holding its
current state. A released revision is a git tag, `blocks/DB-0012/B`, which the
app creates and pushes on release. The revision's letter is written into the
block's marker, so that a placed block names the tag to read it back from.
Revisions are not kept as directories side by side: that would copy every
file for each revision in a repository whose history already holds them, and
KiCad's design block panel would list every revision as a block of its own.

## Considered options

- **A directory per revision** (`USB-C_Input_B.kicad_block`). Every revision
  readable without git, but every one also offered to designers in KiCad,
  and the history of one circuit split across names.
- **Frozen copies under `revisions/<letter>/`** beside the working state.
  Readable without git and hidden from KiCad, but a second copy of what git
  holds, kept in step by hand.
- **No tags; find the commit where the marker's revision changed.** No new
  git objects, but slow, and fooled by an edit made in git by hand. Kept only
  to recreate a missing tag.

## Consequences

- The server's clone must have full history and the tags; the app already
  refuses a shallow clone for part histories.
- The git credentials the server pushes with must be allowed to push tags,
  not only the library's branch.
- Every check on a revision still in use (parts at risk, edited blocks) reads
  the block as it was at its tag, not as it is on the branch.
