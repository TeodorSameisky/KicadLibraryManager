"""The library's files: where each kind is kept, and how each is written down.

    categories.yaml              the category codes an IPN may start with
    templates/<category>.yaml    the fields a part in that category carries
    mpns/<MPN>.yaml              one manufacturer part
    parts/<category>/<IPN>.yaml  one internal part
    products/<family>.yaml       one product family, and the devices in it

Everything that reads these files and everything that writes them comes
through here, so that the two agree: a file this app writes is one it can
read back, in the place it will look for it. What a file's contents *mean*
is not decided here -- that is the loaders', which turn what is read into
parts and report what is wrong with it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from app.domain.templates import TemplateField

# -- where each kind of file is -----------------------------------------------


def relative(root: Path, path: Path) -> str:
    """A file as a maintainer would name it: from the top of the repository,
    and nothing about where the server keeps its clones."""
    return path.relative_to(root).as_posix()


def categories_file(root: Path) -> Path:
    return root / "categories.yaml"


def template_file(root: Path, category: str) -> Path:
    return root / "templates" / f"{category}.yaml"


def template_files(root: Path) -> list[Path]:
    return sorted((root / "templates").glob("*.yaml"))


def mpn_file_stem(mpn: str) -> str:
    """The file name a manufacturer part is kept under, without `.yaml`.

    Usually the part number itself. But part numbers contain slashes, colons
    and spaces -- "LM358/NOPB" -- which a file name cannot, so those become
    underscores.
    """
    return re.sub(r"[^A-Za-z0-9._+#()-]", "_", mpn.strip())


def mpn_file(root: Path, mpn: str) -> Path:
    return root / "mpns" / f"{mpn_file_stem(mpn)}.yaml"


def mpn_files(root: Path) -> list[Path]:
    return sorted((root / "mpns").glob("*.yaml"))


def parts_directory(root: Path) -> Path:
    return root / "parts"


def has_parts(root: Path) -> bool:
    """Whether a repository carries part definitions at all."""
    return parts_directory(root).is_dir()


def part_files(root: Path) -> list[Path]:
    """Every part's file, wherever under parts/ it was filed."""
    return sorted(parts_directory(root).rglob("*.yaml"))


def part_file(root: Path, category: str, ipn: str) -> Path:
    """Where a new part goes: parts/1102/1102-0001.yaml, which keeps a few
    thousand files browsable."""
    return parts_directory(root) / category / f"{ipn}.yaml"


def find_part(root: Path, ipn: str) -> Path | None:
    """Where a part's file is, wherever under parts/ it was filed."""
    found = sorted(parts_directory(root).rglob(f"{ipn}.yaml"))
    return found[0] if found else None


def has_products(root: Path) -> bool:
    """Whether a repository says anything about products at all."""
    return (root / "products").is_dir()


def product_files(root: Path) -> list[Path]:
    return sorted((root / "products").glob("*.yaml"), key=lambda path: path.name.casefold())


# -- reading --------------------------------------------------------------------


class Unreadable(Exception):
    """A file that cannot be read as YAML at all.

    `kind` is the slug the issue list files it under: "invalid-yaml" for one
    that is not YAML, "unreadable" for one that could not be opened.
    """

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind


def parse(text: str) -> Any:
    """What a file's text says, as YAML reads it. An empty file says None."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # The first line says what and where; the rest quotes the file.
        raise Unreadable("invalid-yaml", str(exc).split("\n")[0]) from exc


def read(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Unreadable("unreadable", str(exc)) from exc
    return parse(text)


# -- writing --------------------------------------------------------------------


def write(path: Path, text: str) -> None:
    """Put a file in the library, making the directory it goes in if need be.

    Always with Unix line endings: the file is committed from whatever
    machine this runs on, and should not differ by it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def block(data: Mapping[str, Any]) -> str:
    """Some keys and their values, as lines of YAML in the order given."""
    return yaml.safe_dump(dict(data), sort_keys=False, allow_unicode=True, default_flow_style=False)


def part_text(data: Mapping[str, Any]) -> str:
    """A part as the library's files are laid out: what it is, what it is
    drawn with, what it carries, who makes it -- a blank line between each."""
    known = ("ipn", "description", "status", "symbol", "footprint", "fields", "mpns")
    blocks = [
        block({key: data[key] for key in ("ipn", "description", "status") if key in data}),
        block({key: data[key] for key in ("symbol", "footprint") if data.get(key)}),
    ]
    if data.get("fields"):
        blocks.append(block({"fields": data["fields"]}))
    if data.get("mpns"):
        blocks.append(block({"mpns": data["mpns"]}))
    # Anything else a hand-written file holds is carried along, not dropped.
    extra = {key: value for key, value in data.items() if key not in known}
    if extra:
        blocks.append(block(extra))
    return "\n".join(text for text in blocks if text.strip() != "{}")


def mpn_text(*, mpn: str, manufacturer: str, datasheet: str = "", lifecycle: str) -> str:
    """A manufacturer part: its number, who makes it, where its datasheet is
    if that is known, and how it stands."""
    document = {"mpn": mpn, "manufacturer": manufacturer}
    if datasheet:
        document["datasheet"] = datasheet
    document["lifecycle"] = lifecycle
    return block(document)


# Text that something reading YAML might take for a number: digits, with a
# sign, a decimal point or an exponent. "0805" is one of these, and a reader
# that does not follow this parser's rules would take it for 805.
_NUMBER_LIKE = re.compile(r"^[-+]?[0-9][0-9_.]*([eE][-+]?[0-9]+)?$")


class _Dumper(yaml.SafeDumper):
    """Quotes what could be mistaken for a number, whichever parser reads it."""


def _represent_text(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "'" if _NUMBER_LIKE.match(value) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _represent_text)


def _scalar(value: object) -> str:
    """One value as YAML writes it on a line: quoted only where it has to be.

    "0402" is quoted, because bare it could be read as the number 402, and
    "1%" is not, because nothing can mistake it for anything else.
    """
    dumped = yaml.dump(
        value, Dumper=_Dumper, default_flow_style=True, allow_unicode=True, width=10_000
    )
    return dumped.strip().removesuffix("...").strip()


def template_text(category: str, value: str, fields: Sequence[TemplateField]) -> str:
    """A template's file: which category, how Value is built, then the fields.

    Written by hand rather than dumped, so that each field is a short block
    and its options are on one line, as a person would write them:
    `options: [0.1%, 1%, 5%]`.
    """
    lines = [f"category: {_scalar(category)}"]
    if value.strip():
        lines.append(f"value: {_scalar(value.strip())}")

    if fields:
        lines += ["", "fields:"]
    for spec in fields:
        lines.append(f"  - name: {_scalar(spec.name.strip())}")
        if spec.required:
            lines.append("    required: true")
        if spec.options:
            lines.append(f"    options: {_scalar(list(spec.options))}")
        if spec.example.strip():
            lines.append(f"    example: {_scalar(spec.example.strip())}")
        if spec.default.strip():
            lines.append(f"    default: {_scalar(spec.default.strip())}")
    return "\n".join(lines) + "\n"
