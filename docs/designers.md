# Using the library

For whoever designs boards: getting the library into KiCad, finding a part,
placing it, and asking for one the library does not have yet.

Ask whoever runs the server for its address. It is written below as
`https://library.example.com`; the status page at that address says what it
really is.

- [Connecting KiCad](#connecting-kicad)
- [Finding a part](#finding-a-part)
- [Placing it](#placing-it)
- [A part's page](#a-parts-page)
- [When the library does not have it](#when-the-library-does-not-have-it)
- [BOMs](#boms)

## Connecting KiCad

There are two ways in. Use the panel if you have KiCad 10; the HTTP library is
for KiCad 8 and 9, or for choosing parts in KiCad's own symbol chooser.

| | Remote Symbols panel | HTTP library |
| --- | --- | --- |
| KiCad | 10 and later | 8 and later |
| Symbols and footprints | sent with the part | from your own checkout of the library |
| Signing in | in the panel, through your company login | a token, made once per computer |
| Searching by parameters | yes | no: KiCad's chooser searches text |

### The Remote Symbols panel

1. In KiCad: **Preferences → Remote Symbols**, add
   `https://library.example.com/.well-known/kicad-remote-provider`.
2. Leave the *library prefix* as it is (`remote`), unless whoever runs the
   server says otherwise. KiCad files the footprints it receives under
   `remote_<library>`, and the symbols it places name that library, so the two
   have to agree.
3. Open the panel in the schematic editor. If the library needs a sign-in, the
   panel says so: press **Sign in**, finish in the browser that opens, and come
   back. A session lasts eight hours unless the server is set otherwise.

Nothing has to be checked out. Each part arrives with its symbol, footprint and
3D model, and KiCad keeps them.

### The HTTP library

KiCad's HTTP library gives KiCad a part's fields and the *names* of its
symbol and footprint. It finds the drawings in your own library tables, so you
need the library's repository on your computer first.

1. Clone the library's repository (the status page lists it under *Sources*)
   and keep it pulled.
2. In KiCad: **Preferences → Configure Paths**, set `KICAD_LIBRARY_DIR` to
   where you cloned it. (If the repository's `sym-lib-table` uses another
   variable, set that one.)
3. Add the repository's symbol and footprint libraries under the names its
   `sym-lib-table` and `fp-lib-table` give them. The simplest way is to copy
   those entries into your own global tables.
4. Open `https://library.example.com/kicad-library` in a browser, sign in,
   name the computer (`Office laptop`), press **Make a token**, and download
   the `.kicad_httplib` file. The token is in the file and is shown only this
   once.
5. In KiCad: **Preferences → Manage Symbol Libraries**, add the file as a
   library of type *HTTP*.

Each category is a library in the symbol chooser, with its parts under it.
KiCad reads a part again after a minute; a new category shows after KiCad is
restarted. When you stop using a computer, revoke its token on the same page.

## Finding a part

Type into the search box. It looks at the part number, the description, the
field values, the manufacturer part numbers and who makes them:
`10k 0603`, `RC0603`, `Yageo`, `1102-0001`.

When you do not know the part yet, start from what it has to be:

1. **Choose a category.** The catalog then shows its *parameters*: each thing
   its parts differ in, with a dropdown of the values.
2. **Choose values.** The number beside each value is how many parts would be
   left if you chose it. Values are read as amounts: `4k7` sorts before `10k`,
   and `100mW` is the same as `0.1W`.
3. **Ask for at least or at most.** Where a parameter is an amount, the sign in
   front of it switches between = (exactly), ≥ (at least) and ≤ (at most). A
   voltage rating is wanted to be enough, not exact.

The status tabs narrow to approved parts, drafts or retired ones. A draft is
marked *draft*: it may be placed, but nobody has approved it yet. A retired
part is marked *obsolete* and should not go into a new design.

The search is in the page's address, so a search can be sent to someone:
`/panel?category=1102&where=Tolerance=1%25`.

Keys: <kbd>/</kbd> to search, <kbd>↑</kbd> <kbd>↓</kbd> to move, <kbd>Enter</kbd> to place.

## Placing it

**Place in KiCad** (or <kbd>Enter</kbd> on the selected row) sends the
footprint and 3D model first, then the symbol, which lands on the cursor. What
lands is the part, not a bare symbol:

- **Value** is the part's value, built from its fields where its category says
  how.
- **IPN**, **MPN** and **Manufacturer**, the preferred manufacturer part.
- **Datasheet** is the part's page on this server. Keep it: a BOM exported from
  the schematic is matched to the library by it.
- **Footprint** names `remote_<library>:<name>`, where KiCad saved it.

If a footprint or 3D model cannot be sent, the panel says so before anything is
sent, and the symbol is still placed. A part whose symbol cannot be completed is
not placed at all; it is on the issues page for a librarian.

A large STEP model may take longer than KiCad waits for. The panel warns about
one before sending it.

## A part's page

Click a part's number, or follow the Datasheet link of a placed symbol, which
works in any browser, years later, without KiCad. The page shows:

- the symbol and footprint drawn, with each pin and its pad lit together when
  hovered, and the 3D model on request;
- the manufacturer parts that may be bought for it, with their datasheets;
- **Supply**: stock, status and prices at DigiKey, Mouser and Farnell, where
  the server has keys for them;
- **Where used**: the stored BOMs it is on, which is what to look at before
  changing a design to use something else;
- **History**: every change to the part, by whom and why.

In KiCad's panel, a link to another site (a datasheet PDF) cannot open a
browser; the panel shows the address, copied where it can, to paste into one.
That is KiCad's limit, not the server's.

## When the library does not have it

Ask for it. The part is in the library, as a draft, the moment you save it,
and you can place it straight away. A librarian approves it later.

1. **Look first.** Search for its MPN and its value. Someone may have asked
   already.
2. **Open Request a part** (in a browser; the panel does not offer it).
3. **Choose the category.** The form then shows the fields that category's
   parts carry. Required ones are marked; where only certain values are
   allowed, there is a dropdown.
4. **Fill in the description, the symbol and the footprint.** If the library
   has no symbol or footprint for it, follow the *uploaded* link under the two
   fields: it opens the upload page in a new tab. Upload the vendor's `.kicad_sym`, `.kicad_mod` and
   `.step` there, under a library name (`Regulators`), and come back. The
   form's lists are read again when you return to it.
5. **Add the manufacturer parts** that may be bought for it, one preferred.
   A manufacturer part the library knows already says which parts list it.
6. **Read the box of parts that look like it.** The form compares as you type:
   the same MPN, or the same category, footprint and fields, or the same
   description. If one of them is the part you want, use that one instead.
7. **Say why** in the note, if there is something a librarian should know, and
   save. The note goes into the library's history with the part.

A draft can be corrected by anyone, through *Edit* on its page. Once approved,
only a librarian changes it.

The upload page draws each file before anything is saved, and says what is
wrong with it: a KiCad 5 file (open and save it in KiCad's editor first), a zip
(unpack it), a name another library already uses. Replacing or removing a
symbol or footprint the library already has is for a librarian, since it
changes every part drawn with it.

## BOMs

Export the BOM from the schematic editor (**Tools → Generate Bill of
Materials**; KiCad's default columns are enough) and upload it on the **BOMs**
page with the project's name and revision. Choose the device, if the
**Products** page lists the one this project is.

The stored BOM is read against the library as it is today: a part retired
last month is marked on a BOM from last year. It downloads with the library's
columns added, for purchasing. Its **Cost** page works out what a number of
boards costs from the suppliers' prices; **Ask the suppliers** fills in what is
not known yet, a little at a time.

Uploading the same project and revision again replaces it, after asking.
