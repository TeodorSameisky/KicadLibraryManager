# When something is wrong

Look at the status page first: the server's address, opened in a browser. It
lists every configuration problem the app can detect, each source and whether
it could be read, the suppliers and why they last refused, and when the last
backup was made. Then **Issues**, for what is wrong with the library's files.

An error page or answer from the app carries a request id. Whoever runs the
server finds every log line for that request by it.

- [Connecting KiCad](#connecting-kicad)
- [Signing in](#signing-in)
- [Placing parts](#placing-parts)
- [The HTTP library](#the-http-library)
- [Changing the library](#changing-the-library)
- [BOMs and suppliers](#boms-and-suppliers)
- [The server](#the-server)

## Connecting KiCad

**KiCad will not add the provider.** The address must be the server's
`PUBLIC_URL` followed by `/.well-known/kicad-remote-provider`, exactly. KiCad
checks that every address the server gives belongs to the same site it was
given, so a `PUBLIC_URL` that differs from the address used (`http` against
`https`, a missing path, another host name) fails at once. Open the discovery
address in a browser: the addresses in it must start with the one you typed.

**The panel says the library is syncing.** The first clone is still being
read; a large library with 3D models takes minutes. If it does not end, the
status page says why the source could not be read.

**The panel says "The library could not be indexed".** It could not be read at all.
The status page says which source, and why: a wrong address, a private
repository without a token, a branch that does not exist.

## Signing in

**"Failed to store remote provider tokens securely", after logging in.** The
identity provider's tokens are too large for the operating system's credential
store (on Windows, about 1280 characters for all three). Sign the tokens with
an elliptic-curve key (ES256), ask for the `openid` and `email` scopes only,
and take names and groups out of what the provider puts in them. See
[Keep the tokens small](../README.md#keep-the-tokens-small).

**The browser opens, the login succeeds, and KiCad hears nothing.** The
provider does not accept KiCad's loopback redirect,
`http://127.0.0.1:<any port>/oauth/callback`, or does not allow PKCE on a
public client. GitHub's OAuth Apps cannot do either.

**A part's page, opened from a schematic, fails to sign in.** The browser signs
in with the app itself as the client, and its redirect address,
`<PUBLIC_URL>/auth/callback`, is not registered at the provider. The status
page prints the exact address to register.

**Signed in, but not allowed to approve.** You are not in `LIBRARIANS`, or are
listed under a name the provider does not report. List the e-mail address or
subject id the provider sends, not the display name.

## Placing parts

**The placed symbol's footprint is not found.** KiCad saves the footprints it
receives under `<prefix>_<library>`, and the server writes that name into the
symbol. The prefix in KiCad's Remote Symbol settings and the server's
`REMOTE_LIBRARY_PREFIX` (`remote` unless changed) must be the same.

**A part is not placed at all.** Its symbol cannot be completed: it names a
symbol no source has, or one that `extends` a symbol that is not there. The
part is on the issues page as `missing-symbol` or `unplaceable`.

**A part is placed without its footprint or 3D model.** The panel said so before
sending. The issues page has `missing-footprint` or `missing-model` for it. A
model kept in Git LFS that the server's clone has only as a pointer is
`lfs-pointer`.

**Placing a part with a large 3D model times out.** A STEP file of several
megabytes takes longer to cross into KiCad than KiCad waits. The panel warns
about one. A simpler model is the remedy.

**A datasheet link in the panel does not open.** KiCad's panel keeps links in
itself and cannot open a browser; the panel shows the address, copied where it
can, to paste into one. This is KiCad's limit.

**The panel has no *Request a part*, BOMs or Library.** Those are for a
browser, since the panel has no way back from them. Open the server's address
in a browser.

## The HTTP library

**The categories show, but the symbol chooser has no drawing.** An HTTP library
gives KiCad the names of symbols and footprints, and KiCad looks them up in
your own library tables. Check out the library's repository, set
`KICAD_LIBRARY_DIR` (Preferences → Configure Paths), and add its libraries under
the names its `sym-lib-table` and `fp-lib-table` use. A library that is not in
those files is on the issues page as `library-not-in-table`.

**A new category is not listed.** KiCad reads the categories when it starts.
Restart it.

**KiCad says the library cannot be loaded.** The token in the file has been
revoked, or the file was made for another server. Make a new token at
`<PUBLIC_URL>/kicad-library` and download the file again.

**A part is missing from the HTTP library.** Retired parts are left out, and
so are parts that name no symbol. Drafts are listed, with "Draft:" before their
description.

## Changing the library

**There is no *Request a part*, and forms say the library cannot be changed.**
The server has no `LIBRARY_WRITE_TOKEN`, so it only reads the library.

**Saving fails, and the message quotes the git host.** The write token could
not push. "The token was not accepted": it is mistyped, expired or revoked.
"The token was recognised but may not push": it needs write access to the
repository's contents, or the branch is protected against pushes. Anything
else is the git host's own reason, quoted.

**A change pushed with git does not show.** The app looks again every
`LIBRARY_REFRESH_MINUTES`. A librarian can press **Refresh now** on the status
page, and a webhook makes it immediate; see
[Keeping it current](setting-up.md#5-keeping-it-current). If the status page
shows the new commit and the part is still not there, the issues page says what
is wrong with its file.

**An upload is refused.** The page gives the reason:

- a KiCad 5 symbol library (`.lib`) or footprint (`(module`): open it in
  KiCad's editor and save it;
- a zip: unpack it and upload the files;
- a name another source already uses: the first source's would be used, and
  the upload hidden;
- a 3D model, where the repository keeps models in Git LFS: add the model with
  git, then upload the footprint alone;
- replacing a symbol or footprint the library has: a librarian's to do.

**A part the form says looks like another.** Nothing is refused for it. If it
is the same part, use the existing one; if not, say in the note how it differs.

## BOMs and suppliers

**An uploaded BOM recognises no parts.** Parts are recognised by an `IPN`
column, or by the `Datasheet` column, which a symbol placed from this library
fills with the part's page. A BOM exported with neither, or from symbols not
placed from the library, has nothing to go by.

**A part's page shows nothing under Supply.** The server has no key for any
supplier, or the supplier refused it; the status page says which, in the
supplier's words. When a supplier's calls for the day are used up, the last
answer kept is shown with its date.

**Asking the suppliers for a BOM stops before the end.** A supplier allows so
many calls a minute. Asking again later asks only what is still missing.

## The server

**The status page says the database was written by a newer version.** The app
was taken back a version after an upgrade. Go forward again, or restore the
backup from before the upgrade.

**The status page says the database cannot be opened.** The directory of
`DATABASE_PATH` does not exist or is not writable by uid 1000. BOMs cannot be
stored until it is.

**The last backup is overdue.** The app has not been able to make one for
three days. The log says why; the backup directory has to be writable by uid
1000, and the disk must have room.

**The container restarts.** `/healthz` reports only the process, so the library
being unreachable never restarts it. The log has the reason; set
`LOG_LEVEL=DEBUG` for more.
