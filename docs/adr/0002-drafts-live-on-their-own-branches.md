---
status: accepted
---

# Draft revisions live on their own branches

Because a design block is one directory (ADR 0001), whatever is on the
library's branch is what every designer's clone places. A draft revision is
therefore kept on a branch of its own, `drafts/DB-0012-C`, which the app
merges into the library's branch and tags on release. The library's branch
only ever holds released revisions. Drafts of parts stay on the library's
branch with a status, as before: a draft part is not placed by accident from
a clone, a draft block would be.

## Considered options

- **Drafts on the library's branch**, marked `C (draft)` in the marker, with
  a warning on any BOM that carries one. Simpler, and how parts work, but a
  designer who pulls gets the unfinished circuit in KiCad's panel.
- **Drafts held by the app, outside git, until release.** Nothing unreleased
  reaches the repository, but a draft's own changes leave no history and
  live only on the server.

## Consequences

- The app works with more than one branch: it reads, shows and checks drafts
  from their branches, commits to them, and merges them. Until now it pushes
  to the library's branch only.
- A release can fail to merge if the block's directory was changed on the
  library's branch since the draft was started; the release then has to be
  redone on the newer state. One directory per block makes this rare.
