"""Internal part numbers and the manufacturer parts that satisfy them.

An IPN is what gets placed in KiCad. It names a symbol and a footprint from the
asset libraries, carries the field values, and lists the approved manufacturer
parts.

MPNs are separate documents rather than embedded in each IPN because one MPN
can satisfy several IPNs. That also makes obsolescence answerable: a lifecycle
change is one edit, and the reverse index says which parts it affects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from app.kicad.issues import Issue, Severity, errors, warnings

IPN_PATTERN = re.compile(r"^(?P<category>[A-Za-z0-9]+)-(?P<number>\d+)$")


class AssetResolver(Protocol):
    """What this module needs of a catalog, without depending on one.

    Parts are loaded before anything knows how many sources exist, and the
    catalog already knows about libraries; importing it here would make the
    dependency circular for the sake of two lookups.
    """

    def find_symbol(self, library: str, name: str) -> list: ...

    def find_footprint(self, library: str, name: str) -> list: ...


PART_STATUSES = ("draft", "approved", "obsolete")
LIFECYCLES = ("active", "nrnd", "obsolete", "unknown")


@dataclass(frozen=True)
class Category:
    code: str
    name: str


@dataclass
class Mpn:
    mpn: str
    manufacturer: str
    description: str
    datasheet: str
    lifecycle: str
    path: Path


@dataclass
class MpnRef:
    mpn: str
    preferred: bool = False


@dataclass
class Part:
    ipn: str
    description: str
    status: str
    symbol: str
    footprint: str
    fields: dict[str, str]
    mpns: list[MpnRef]
    path: Path

    @property
    def category(self) -> str:
        match = IPN_PATTERN.match(self.ipn)
        return match.group("category") if match else ""

    @property
    def preferred_mpn(self) -> str | None:
        for ref in self.mpns:
            if ref.preferred:
                return ref.mpn
        return self.mpns[0].mpn if self.mpns else None


@dataclass
class PartsIndex:
    root: Path
    categories: dict[str, Category] = field(default_factory=dict)
    mpns: dict[str, Mpn] = field(default_factory=dict)
    parts: dict[str, Part] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return errors(self.issues)

    @property
    def warnings(self) -> list[Issue]:
        return warnings(self.issues)

    def parts_using(self, mpn: str) -> list[Part]:
        """Every IPN listing this manufacturer part.

        The question asked when a manufacturer discontinues something.
        """
        return sorted(
            (p for p in self.parts.values() if any(r.mpn == mpn for r in p.mpns)),
            key=lambda p: p.ipn,
        )


def _load_yaml(path: Path, index: PartsIndex) -> Any | None:
    rel = path.relative_to(index.root).as_posix()
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        index.issues.append(Issue(Severity.ERROR, "invalid-yaml", str(exc).split("\n")[0], rel))
    except (OSError, UnicodeDecodeError) as exc:
        index.issues.append(Issue(Severity.ERROR, "unreadable", str(exc), rel))
    return None


def _load_categories(root: Path, index: PartsIndex) -> None:
    path = root / "categories.yaml"
    if not path.is_file():
        index.issues.append(
            Issue(
                Severity.WARNING,
                "no-categories",
                "categories.yaml is missing; IPN category codes cannot be checked",
            )
        )
        return

    data = _load_yaml(path, index)
    if data is None:
        return
    if not isinstance(data, list):
        index.issues.append(
            Issue(
                Severity.ERROR, "bad-categories", "expected a list of categories", "categories.yaml"
            )
        )
        return

    for entry in data:
        if not isinstance(entry, dict) or not entry.get("code"):
            index.issues.append(
                Issue(
                    Severity.ERROR, "bad-category", f"malformed entry: {entry!r}", "categories.yaml"
                )
            )
            continue
        code = str(entry["code"])
        index.categories[code] = Category(code=code, name=str(entry.get("name") or code))


def _load_mpns(root: Path, index: PartsIndex) -> None:
    for path in sorted((root / "mpns").glob("*.yaml")) if (root / "mpns").is_dir() else []:
        rel = path.relative_to(root).as_posix()
        data = _load_yaml(path, index)
        if data is None:
            continue
        if not isinstance(data, dict) or not data.get("mpn"):
            index.issues.append(Issue(Severity.ERROR, "bad-mpn", "missing 'mpn'", rel))
            continue

        mpn = str(data["mpn"])
        if mpn != path.stem:
            index.issues.append(
                Issue(Severity.WARNING, "name-mismatch", f"mpn {mpn!r} in file {path.stem!r}", rel)
            )

        lifecycle = str(data.get("lifecycle") or "unknown")
        if lifecycle not in LIFECYCLES:
            index.issues.append(
                Issue(
                    Severity.WARNING,
                    "bad-lifecycle",
                    f"lifecycle {lifecycle!r} is not one of {', '.join(LIFECYCLES)}",
                    rel,
                )
            )

        if mpn in index.mpns:
            index.issues.append(
                Issue(Severity.ERROR, "duplicate-mpn", f"{mpn} already defined", rel)
            )
            continue

        index.mpns[mpn] = Mpn(
            mpn=mpn,
            manufacturer=str(data.get("manufacturer") or ""),
            description=str(data.get("description") or ""),
            datasheet=str(data.get("datasheet") or ""),
            lifecycle=lifecycle,
            path=path,
        )


def _parse_mpn_refs(raw: Any, rel: str, index: PartsIndex) -> list[MpnRef]:
    refs: list[MpnRef] = []
    if raw is None:
        return refs
    if not isinstance(raw, list):
        index.issues.append(Issue(Severity.ERROR, "bad-mpns", "'mpns' must be a list", rel))
        return refs

    for entry in raw:
        if isinstance(entry, str):
            refs.append(MpnRef(mpn=entry))
        elif isinstance(entry, dict) and entry.get("mpn"):
            refs.append(MpnRef(mpn=str(entry["mpn"]), preferred=bool(entry.get("preferred"))))
        else:
            index.issues.append(
                Issue(Severity.ERROR, "bad-mpn-ref", f"malformed entry: {entry!r}", rel)
            )
    return refs


def _load_parts(root: Path, index: PartsIndex) -> None:
    parts_dir = root / "parts"
    if not parts_dir.is_dir():
        return

    for path in sorted(parts_dir.rglob("*.yaml")):
        rel = path.relative_to(root).as_posix()
        data = _load_yaml(path, index)
        if data is None:
            continue
        if not isinstance(data, dict) or not data.get("ipn"):
            index.issues.append(Issue(Severity.ERROR, "bad-part", "missing 'ipn'", rel))
            continue

        ipn = str(data["ipn"])
        if ipn != path.stem:
            index.issues.append(
                Issue(Severity.ERROR, "name-mismatch", f"ipn {ipn!r} in file {path.stem!r}", rel)
            )

        match = IPN_PATTERN.match(ipn)
        if not match:
            index.issues.append(
                Issue(Severity.ERROR, "bad-ipn", f"{ipn!r} is not <category>-<number>", rel)
            )
        else:
            category = match.group("category")
            if index.categories and category not in index.categories:
                index.issues.append(
                    Issue(
                        Severity.ERROR,
                        "unknown-category",
                        f"category {category!r} is not declared in categories.yaml",
                        rel,
                    )
                )
            # parts/1102/1102-0001.yaml keeps a few thousand files browsable.
            if path.parent.name != category and path.parent != parts_dir:
                index.issues.append(
                    Issue(
                        Severity.WARNING,
                        "misfiled",
                        f"{ipn} is under {path.parent.name!r}, expected {category!r}",
                        rel,
                    )
                )

        status = str(data.get("status") or "draft")
        if status not in PART_STATUSES:
            index.issues.append(
                Issue(
                    Severity.WARNING,
                    "bad-status",
                    f"status {status!r} is not one of {', '.join(PART_STATUSES)}",
                    rel,
                )
            )

        raw_fields = data.get("fields") or {}
        fields = (
            {str(k): str(v) for k, v in raw_fields.items()} if isinstance(raw_fields, dict) else {}
        )
        if raw_fields and not isinstance(raw_fields, dict):
            index.issues.append(
                Issue(Severity.ERROR, "bad-fields", "'fields' must be a mapping", rel)
            )

        refs = _parse_mpn_refs(data.get("mpns"), rel, index)
        if len([r for r in refs if r.preferred]) > 1:
            index.issues.append(
                Issue(
                    Severity.ERROR,
                    "multiple-preferred",
                    "more than one MPN is marked preferred",
                    rel,
                )
            )

        part = Part(
            ipn=ipn,
            description=str(data.get("description") or ""),
            status=status,
            symbol=str(data.get("symbol") or ""),
            footprint=str(data.get("footprint") or ""),
            fields=fields,
            mpns=refs,
            path=path,
        )

        if ipn in index.parts:
            index.issues.append(
                Issue(Severity.ERROR, "duplicate-ipn", f"{ipn} already defined", rel)
            )
            continue
        index.parts[ipn] = part


def _check_internal(index: PartsIndex) -> None:
    for part in index.parts.values():
        rel = part.path.relative_to(index.root).as_posix()

        if not part.symbol:
            index.issues.append(
                Issue(Severity.ERROR, "no-symbol", f"{part.ipn} names no symbol", rel)
            )
        if not part.description:
            index.issues.append(
                Issue(
                    Severity.WARNING,
                    "no-description",
                    f"{part.ipn} has no description; the number alone is unsearchable",
                    rel,
                )
            )
        if not part.mpns and part.status == "approved":
            index.issues.append(
                Issue(
                    Severity.WARNING,
                    "no-mpns",
                    f"{part.ipn} is approved but lists no manufacturer part",
                    rel,
                )
            )

        for ref in part.mpns:
            if ref.mpn not in index.mpns:
                index.issues.append(
                    Issue(
                        Severity.ERROR,
                        "unknown-mpn",
                        f"{part.ipn} references {ref.mpn!r}, which has no file in mpns/",
                        rel,
                    )
                )
            elif index.mpns[ref.mpn].lifecycle == "obsolete" and part.status == "approved":
                index.issues.append(
                    Issue(
                        Severity.WARNING,
                        "obsolete-mpn",
                        f"{part.ipn} is approved but {ref.mpn} is obsolete",
                        rel,
                    )
                )


def load_parts(root: Path) -> PartsIndex:
    """Load categories, MPNs and IPNs, checking everything internal to them."""
    index = PartsIndex(root=root)
    _load_categories(root, index)
    _load_mpns(root, index)
    _load_parts(root, index)
    _check_internal(index)
    return index


def check_against_catalog(index: PartsIndex, catalog: AssetResolver) -> None:
    """Resolve each part's symbol and footprint against the asset catalog.

    Separate from loading because the assets may live in other repositories
    entirely, so this can only run once every source is indexed.
    """
    for part in index.parts.values():
        rel = part.path.relative_to(index.root).as_posix()

        for kind, ref, finder in (
            ("symbol", part.symbol, catalog.find_symbol),
            ("footprint", part.footprint, catalog.find_footprint),
        ):
            if not ref:
                continue
            if ":" not in ref:
                index.issues.append(
                    Issue(
                        Severity.ERROR,
                        f"unqualified-{kind}",
                        f"{kind} {ref!r} has no library prefix",
                        rel,
                    )
                )
                continue
            library, name = ref.split(":", 1)
            if not finder(library, name):
                index.issues.append(
                    Issue(
                        Severity.ERROR,
                        f"missing-{kind}",
                        f"{part.ipn} references {kind} {ref!r}, " "which no source provides",
                        rel,
                    )
                )
