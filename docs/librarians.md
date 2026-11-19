# Keeping the library

For librarians: whoever decides what goes into the library. You are one if
your username, e-mail address or subject id is in the server's `LIBRARIANS`
setting; ask whoever runs the server. With sign-in switched off, everyone is
one.

Designers can ask for a part, upload what it is drawn with, and correct a draft
without you. What is yours is deciding: approving a part, changing or retiring
an approved one, replacing or removing a drawing, keeping the templates and the
list of manufacturers, deleting a BOM, revoking anyone's token.

Everything you do here is a commit to the library's repository, in your name,
with the note you give as its message body. There is no other record: the
repository's history is the audit trail, and a part's *History* page reads it.

- [The worklist](#the-worklist)
- [Approving a part](#approving-a-part)
- [Changing, retiring and reopening](#changing-retiring-and-reopening)
- [Templates](#templates)
- [Manufacturers](#manufacturers)
- [Symbols, footprints and 3D models](#symbols-footprints-and-3d-models)
- [Tokens, BOMs and backups](#tokens-boms-and-backups)
- [Working in git directly](#working-in-git-directly)

## The worklist

Two places say what needs doing:

- **The catalog, *draft* tab.** Every part asked for and not yet approved.
- **Issues.** Everything in the library that does not line up, grouped by kind,
  errors and warnings apart. Under each issue is the part it is felt in, with *Edit* beside
  it. [The library's files](library-files.md#what-is-checked) lists every kind
  and what to do about it.

A good habit is to look at both once a week, and at the issues after any large
change pushed by hand.

## Approving a part

Open the part from the catalog. Approving says it may be designed in, so look
at what a designer will rely on:

1. **The number's category.** A capacitor under the resistors' code cannot be
   moved: the number stays. If the category is wrong, retire the part and ask
   for it again in the right one.
2. **The description**, written the way the category's other parts are. It is
   what a designer reads in the catalog and what the HTTP library shows.
3. **The symbol and footprint.** Hover the pins on the drawing: each lights the
   pad it is connected to. Check the pin numbers against the datasheet's
   pinout, and the footprint's pads against its land pattern.
4. **The fields**, against the category's template. The form will not have let
   a required field be left out, but it cannot tell `10k` from `1k`.
5. **The manufacturer parts.** Each is a real part number, from the
   manufacturer named, in the package the footprint is for. One is preferred:
   the one placed on the schematic and bought by default.
6. **Duplicates.** If the issues page has a `duplicate-part` for this number,
   the library already had it. Retire the later one and tell whoever asked.

Fix what is wrong with *Edit*, or leave the draft for whoever asked to fix.
Then **Approve**, and say what you checked in the note. From then on only a
librarian changes the part.

## Changing, retiring and reopening

- **Edit** an approved part when a correction does not change what the part
  is: a better description, a field that was missing, another manufacturer part
  that may be bought for it.
- **Retire** it when it should not go into new designs: every manufacturer part
  for it is obsolete, or it was a duplicate. Look at *Where used* first; those
  BOMs will show it as obsolete from then on. A retired part stays in the
  library, so the boards that use it still read.
- **Return to draft** when an approved part turns out to be wrong and needs
  working on. Anyone can then correct it again, and it needs approving again.
- **Reinstate** a retired part that was retired by mistake.

Never change an approved part into a different part (a 10k into a 22k): the
boards that carry it would change with it. Ask for a new number.

## Templates

A category's template says what each of its parts has to say about itself:
which fields, which of them are required, the values a field is limited to, and
how Value is built (`{Resistance}`). Without one, a category asks nothing.

Open **Templates**. Each category is listed with how many fields its template
has and how many of its parts fall short of it. In the editor, add the fields,
order them as a designer should fill them in, mark the required ones, and give
`options` where only certain values are allowed. While you edit, the page says
how many existing parts the change would leave short.

Parts that fall short are not refused or hidden: they still place, and are
listed on the issues page under `template`. That list is the work that a new
required field creates, so add one when you mean to fill it in for the parts
that exist.

## Manufacturers

The list of manufacturers gives each company one name, with the other ways it
gets written: `TI` and `Texas Instr.` for Texas Instruments. The suppliers are
matched by it, and the catalog and exports show the one name.

Open **Manufacturers**. At the top is the worklist: every name the manufacturer
parts give that the list does not recognise, the most used first.

- **Add to the list** for a company the list does not have.
- **It is this one** for another way of writing one it has.

Below it are the listed manufacturers that some files write another way.
**Write as …** changes those files to say the list's name, one commit for all
of them; **Rewrite all** does every manufacturer at once. Only the
`manufacturer:` line of each file is touched.

## Symbols, footprints and 3D models

**Library** lists every symbol, footprint and 3D model, with what uses each.
Designers may upload what the library does not have. What is yours:

- **Replacing** one that exists. **Replace** on its page opens the upload with
  its library filled in; upload the new file under the same name. The page lists every part drawn with it, and asks you to say that
  it should be replaced. Every board that places those parts from then on gets
  the new drawing; boards already drawn keep theirs.
- **Removing** one nothing uses: no part, no symbol that extends it, no
  footprint that names the model. Its page has **Remove** once that is so.

A repository that keeps its 3D models in Git LFS cannot take them through the
app. Add the model with git, then upload its footprint.

A library begun by an upload is added to the repository's `sym-lib-table` and
`fp-lib-table` in the same commit, so the designers who use the HTTP library
get it with their next pull.

## Tokens, BOMs and backups

- **Tokens.** On the KiCad library page you see everyone's HTTP library tokens,
  with when each was last used. Revoke the tokens of someone who has left, and
  any not used for months.
- **BOMs.** Delete a BOM uploaded by mistake on its page. It cannot be undone,
  except from a backup.
- **Backups.** The server copies its database daily, on its own disk. Download
  a copy from the status page or the BOMs page now and then, and keep it
  somewhere else. It holds the BOMs, which nobody can upload again once the
  files are gone. The library itself is in git and needs nothing from you.
- **Refresh now**, on the status page, reads the library's repository at once.
  Use it after pushing by hand, if the server has no webhook.

## Working in git directly

Nothing stops anyone with access from editing the repository by hand, and the
app reads what is pushed the same way as what it wrote. Large changes are often
easier that way: renaming a library, importing a hundred parts.

- Keep to the [file formats](library-files.md). After pushing, refresh and look
  at the issues page: what does not line up is listed there.
- Write the commit message as the app does: a subject saying what changed, and
  why in the body. A part's history shows the body as the note.
- Files the app later edits are laid out its own way, so comments in a part,
  template or the list of manufacturers do not survive an edit made in the app.
- Do not approve parts by hand to skip the review: an approval is a librarian's
  decision, and the history should show whose.
