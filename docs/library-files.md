# The library's files

The library is a git repository of plain files: YAML for what the parts are,
KiCad's own formats for how they are drawn. This is every file the app reads,
every key in it, and everything it checks. The app writes the same files when
parts are asked for in it, so a file written by hand and one written by the
app look alike.

- [Where things go](#where-things-go)
- [categories.yaml](#categoriesyaml)
- [parts/](#parts)
- [mpns/](#mpns)
- [templates/](#templates)
- [manufacturers.yaml](#manufacturersyaml)
- [products/](#products)
- [Symbols, footprints and 3D models](#symbols-footprints-and-3d-models)
- [Several repositories](#several-repositories)
- [What is checked](#what-is-checked)

## Where things go

```
categories.yaml                                  required, in practice
manufacturers.yaml                               optional
templates/<category>.yaml                        optional, one per category
parts/<category>/<IPN>.yaml                      the internal parts
mpns/<MPN>.yaml                                  the manufacturer parts
products/<family>.yaml                           optional
symbols/<Library>.kicad_symdir/<Name>.kicad_sym
footprints/<Library>.pretty/<Name>.kicad_mod
3dmodels/<Library>.3dshapes/<Name>.step          or .wrl
sym-lib-table, fp-lib-table                      optional: for the HTTP library
```

The three asset directories may instead be at the top of the repository
(`Passives.kicad_symdir/`, `Passives.pretty/`), as KiCad's own libraries are.

A library to start from is these two files and one part:

```yaml
# categories.yaml
- code: "1102"
  name: Resistors, fixed
```

```yaml
# parts/1102/1102-0001.yaml
ipn: 1102-0001
description: Resistor 10k 1% 0.1W 0603
status: draft
symbol: Passives:R
footprint: Passives:R_0603_1608Metric
fields:
  Value: 10k
```

with `symbols/Passives.kicad_symdir/R.kicad_sym` and
`footprints/Passives.pretty/R_0603_1608Metric.kicad_mod` beside them.

Write numbers that are codes as text, in quotes: `"1102"`, `"0603"`. YAML
reads `0603` as the number 603 otherwise, and `1e3` as a thousand.

## categories.yaml

The category codes an IPN may start with, and what each is called.

```yaml
- code: "1101"
  name: Capacitors, fixed
- code: "1102"
  name: Resistors, fixed
```

| Key | | |
| --- | --- | --- |
| `code` | required | Letters and digits. The part of an IPN before the dash. |
| `name` | | What the catalog, the HTTP library and the forms call it. The code, if not given. |

Without this file any code is accepted, and the issues page says that nothing
can be checked (`no-categories`).

## parts/

One file per internal part, named after it, in a directory named after its
category: `parts/1102/1102-0001.yaml`.

```yaml
ipn: 1102-0001
description: Resistor 10k 1% 0.1W 0603
status: approved

symbol: Passives:R_0603
footprint: Passives:R_0603_1608Metric

fields:
  Value: 10k
  Tolerance: 1%

mpns:
  - mpn: RC0603FR-0710KL
    preferred: true
  - mpn: CRCW060310K0FKEA
```

| Key | | |
| --- | --- | --- |
| `ipn` | required | `<category>-<number>`, the number in digits. The same as the file's name. |
| `description` | | One line, as the catalog lists it. Without one the part can only be found by its number. |
| `status` | | `draft`, `approved` or `obsolete`. `draft` if not given. |
| `symbol` | required | `<Library>:<Name>` of a symbol some source has. |
| `footprint` | | `<Library>:<Name>` of a footprint some source has. Without one, no footprint is sent and the symbol keeps its own Footprint field. |
| `fields` | | Name and value of each field the placed symbol carries: `Value`, `Tolerance`. Text, every one. |
| `mpns` | | The manufacturer parts that may be bought for it. Each `{mpn, preferred}`, or just the number. At most one is preferred. |

`IPN`, `Description`, `Datasheet`, `Footprint`, `MPN` and `Manufacturer` are
filled in from the part on placing, so they do not belong under `fields`. A
part without a `Value` field takes one from its category's template.

A new part's number is the next after the highest in its category, at least
four digits wide: `1102-0001`, then `1102-0002`. Retire a part rather than
delete its file: BOMs refer to it, and the highest number deleted would be
given out again.

Keys the app does not know are kept when it rewrites a file.

## mpns/

One file per manufacturer part, named after its number. Characters a file
name cannot have become `_`: `LM358/NOPB` is `mpns/LM358_NOPB.yaml`.

```yaml
mpn: RC0603FR-0710KL
manufacturer: Yageo
datasheet: https://www.yageo.com/upload/media/product/products/datasheet/rchip/PYu-RC_Group_51_RoHS_L_12.pdf
lifecycle: active
```

| Key | | |
| --- | --- | --- |
| `mpn` | required | The manufacturer's part number, exactly. Suppliers are asked for it. |
| `manufacturer` | | Who makes it. Matched against `manufacturers.yaml` where there is one. |
| `datasheet` | | A link to the datasheet. |
| `lifecycle` | | `active`, `nrnd` (not for new designs), `obsolete` or `unknown`. `unknown` if not given. |
| `description` | | Optional. |

A manufacturer part is a file of its own because one may serve several
internal parts. The form for a part never rewrites one that exists.

## templates/

What every part of one category has to say, in a file named after it:
`templates/1102.yaml`. A category without one asks nothing.

```yaml
value: "{Resistance}"
fields:
  - name: Resistance
    required: true
    example: 10k
  - name: Tolerance
    required: true
    options: [0.1%, 1%, 5%]
    default: 1%
  - name: Power
    example: 0.1W
  - Package
```

| Key | | |
| --- | --- | --- |
| `category` | | The category's code. Optional; if given it must be the file's name. |
| `value` | | How Value is built from the fields: names in braces, `{Resistance} {Tolerance}`. Used for a part with no Value of its own. |
| `fields` | | The fields, in the order the form shows them. Each a name, or a mapping: |
| `fields[].name` | required | The field's name. Not one of those filled in on placing. |
| `fields[].required` | | `true` if a part must give it. |
| `fields[].options` | | The only values allowed. The form offers them as a list. |
| `fields[].example` | | Shown in the empty field. |
| `fields[].default` | | What a new part starts with. |

A part may carry fields its template does not name.

## manufacturers.yaml

Each manufacturer under one name, with the other ways it gets written.

```yaml
- name: Texas Instruments
  aliases: [TI, Texas Instr.]
- name: Yageo
- Würth Elektronik
```

| Key | | |
| --- | --- | --- |
| `name` | required | The name everything shows. |
| `aliases` | | Other spellings. Case, accents, punctuation and a legal form at the end (`Inc.`, `GmbH`) are ignored without one. |

A spelling may belong to one manufacturer only.

## products/

One file per product family, named after it: `products/akd.yaml` is the
family `akd`.

```yaml
name: AKD Servodrive
description: Digital servo drives
repository: https://github.com/acme/akd
tags: [servo, drive]
devices:
  - id: mv-3
    name: AKD MV 3
    repository: https://github.com/acme/akd-mv-3
    tags: [mv, 3A]
  - id: hv-6
    name: AKD HV 6
```

| Key | | |
| --- | --- | --- |
| `name`, `description` | | What the family is called and is. |
| `repository` | | A web address. Shown as text, and reported, if it is not one. |
| `tags` | | A list. A family's tags count for each of its devices. |
| `devices` | | The devices. Each has an `id` (letters, digits, `.`, `_`, `-`; unique in the family) and may have `name`, `description`, `repository` and `tags`. |

A device is named by family and id: `akd/mv-3`. BOMs are attached to devices in
the app; nothing about them is in these files.

## Symbols, footprints and 3D models

KiCad's own formats, as its editors save them. KiCad 6 and later: a KiCad 5
`.lib` or a footprint that starts `(module` is not read; open it in KiCad's
editor and save it.

- **Symbols**: one symbol to a file, `symbols/<Library>.kicad_symdir/<Name>.kicad_sym`.
  A symbol may `extends` another of the same library. The name in the file is
  the file's name. A library kept as one `Passives.kicad_sym` file is not
  read: upload it through the app, which splits it into one file a symbol.
- **Footprints**: `footprints/<Library>.pretty/<Name>.kicad_mod`.
- **3D models**: STEP or VRML in `3dmodels/<Library>.3dshapes/`. A footprint
  names its model through a path variable, the one the library's other
  footprints use: `${KICAD10_3DMODEL_DIR}/Passives.3dshapes/R_0603.step`.

A symbol's or footprint's name in a part is `<Library>:<Name>`, the library
being the directory without its suffix.

**3D models in Git LFS** are read only if the server's clone has them; one
that is still an LFS pointer is reported (`lfs-pointer`). The app cannot upload
into LFS.

**`sym-lib-table` and `fp-lib-table`** at the top of the repository are for
whoever uses the HTTP library, which finds the drawings in a designer's own
tables. When they exist, every library in the repository should be in them;
one that is not is reported (`library-not-in-table`). The panel does not need
them.

## Several repositories

`LIBRARY_SOURCES` may list several repositories: the company's, KiCad's own
libraries, a vendor's. Symbols, footprints and models are read from all of
them, so a part may use a footprint from KiCad's libraries. The parts,
manufacturer parts, categories, templates, manufacturers and products are read
from one: the first that has a `parts/` directory. A `parts/` directory in a
later one is ignored, and reported (`parts-ignored`).

When two sources have a footprint under the same name, the first source's is
used (`ambiguous-footprint`).

## What is checked

Each time the library is read, everything is checked, and what does not line
up is listed on the issues page with the file it is in. An **error** is
something that does not work: a part that cannot be placed, a file that cannot
be read. A **warning** is something that works but should be put right. Nothing
here stops the rest of the library from being served.

### Files

| Kind | | What to do |
| --- | --- | --- |
| `invalid-yaml`, `unreadable` | error | The file is not YAML, or not UTF-8. The message says the line. |
| `bad-part`, `bad-mpn`, `bad-category`, `bad-categories` | error | A required key is missing, or the file is not shaped as above. |
| `bad-fields`, `bad-mpns`, `bad-mpn-ref` | error | `fields` must be a mapping; `mpns` a list of numbers or `{mpn, preferred}`. |
| `bad-template` | error | A template field without a name, listed twice, or `options` that is not a list. |
| `bad-manufacturer`, `bad-manufacturers`, `duplicate-manufacturer` | error | An entry with no name, a name listed twice, or a spelling given to two manufacturers. |
| `bad-product`, `repository-not-a-link` | error, warning | A device without an id, listed twice, or a repository that is not a web address. |
| `name-mismatch` | error | The `ipn` in the file is not the file's name. (For a symbol, a warning: the name in the file is not the file's.) |
| `misfiled` | warning | The part is in another category's directory. Move the file. |
| `no-categories` | warning | There is no `categories.yaml`. |

### Parts

| Kind | | What to do |
| --- | --- | --- |
| `bad-ipn` | error | The number is not `<category>-<number>`. |
| `unknown-category` | error | The category is not in `categories.yaml`. Add it, or the part is in the wrong one. |
| `duplicate-ipn` | error | Two files give the same number. |
| `no-symbol`, `unqualified-symbol`, `missing-symbol` | error | The part names no symbol, or not as `<Library>:<Name>`, or one no source has. Upload it, or correct the name. |
| `unplaceable` | error | The symbol's `extends` chain does not reach a symbol with a body. |
| `unqualified-footprint`, `missing-footprint` | error | The same, for the footprint. |
| `multiple-preferred` | error | More than one manufacturer part is preferred. |
| `unknown-mpn` | error | The part lists a manufacturer part with no file in `mpns/`. |
| `no-description` | warning | Give it one: the number alone cannot be searched for. |
| `template` | warning | A field its category's template requires is missing, or not one of the values allowed. |
| `no-mpns` | warning | An approved part lists nothing that can be bought. |
| `obsolete-mpn` | warning | An approved part lists a manufacturer part that is obsolete. Add another, or retire the part. |
| `duplicate-part` | warning | Same category, footprint and fields as an earlier part, or the same preferred manufacturer part. Said of the later number. |
| `bad-status` | warning | The status is not `draft`, `approved` or `obsolete`. |

### Manufacturer parts

| Kind | | What to do |
| --- | --- | --- |
| `duplicate-mpn` | error | Two files give the same number. |
| `bad-lifecycle` | warning | Not `active`, `nrnd`, `obsolete` or `unknown`. |
| `unknown-manufacturer` | warning | The manufacturer is not in `manufacturers.yaml`. Add it, or say which one it is, on the Manufacturers page. |
| `manufacturer-alias` | warning | The file writes a listed manufacturer another way. *Write as …* on the Manufacturers page. |
| `manufacturer-spelling` | warning | Without a list: one name is written more than one way. Start a `manufacturers.yaml`. |

### Symbols, footprints and models

| Kind | | What to do |
| --- | --- | --- |
| `unparseable`, `unexpected-content` | error | Not a KiCad file, or not the kind its extension says. |
| `empty`, `unnamed` | error | A symbol file with no symbol, or a symbol with no name. |
| `duplicate-symbol`, `duplicate-footprint` | error | Two files in one library give the same name. |
| `broken-inheritance` | error | A symbol extends one that is not there. |
| `missing-footprint` | error | A symbol's own Footprint field names one no source has. |
| `missing-model` | error | A footprint names a 3D model no source has. The part still places, without it. |
| `lfs-pointer` | error | The model is in Git LFS and the server's clone has only the pointer. |
| `multiple-symbols` | warning | Several symbols in one `.kicad_sym`; only the first is used. Upload the file through the app, which splits it. |
| `unqualified-footprint` | warning | A symbol's Footprint field has no library prefix. |
| `ambiguous-footprint` | warning | Two sources have a footprint of that name; the first source's is used. |
| `library-not-in-table` | warning | A library is not in `sym-lib-table` or `fp-lib-table`. Add its line. |
| `parts-ignored` | warning | A later source has a `parts/` directory; parts are read from the first only. |
