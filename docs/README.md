# Documentation

The [README](../README.md) describes everything the app does and why. These
guides are the same app from where each reader stands: what to do, in what
order, and where to read more.

| If you | Read |
| --- | --- |
| design boards and want parts out of the library | [Using the library](designers.md) |
| approve parts and keep the library in order | [Keeping the library](librarians.md) |
| write or review the library's files in git | [The library's files](library-files.md) |
| run the server | [Setting it up](setting-up.md) |
| have something not working | [When something is wrong](troubleshooting.md) |
| change the app's code | [Working on the app](development.md) |
| want to know what we would ask KiCad for | [What we would ask KiCad for](kicad-feature-ideas.md) |
| want to know how design blocks will work (not built yet) | [Design blocks](specs/design-blocks.md) |

The words the app uses are defined in [CONTEXT.md](../CONTEXT.md); decisions
that are hard to undo are recorded in [adr/](adr/).

Three words come up everywhere:

- **IPN**, the internal part number: `1102-0001`. What a designer places. It
  names a symbol and a footprint, and says what the part is.
- **MPN**, a manufacturer part number: `RC0603FR-0710KL`. What can be bought
  for an IPN. One IPN usually allows several; one MPN may serve several IPNs.
- **Librarian**: someone listed in `LIBRARIANS`, who decides what goes into
  the library. Everyone else signed in can ask for things and correct drafts.
