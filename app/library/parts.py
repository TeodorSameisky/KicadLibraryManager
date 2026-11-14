"""Internal part numbers and the manufacturer parts that satisfy them.

An IPN is what gets placed in KiCad. It names a symbol and a footprint from
the asset libraries, carries the field values, and lists the manufacturer
parts approved for it.

MPNs are documents of their own rather than entries inside each IPN, because
one MPN can satisfy several IPNs. That is also what makes obsolescence
answerable: a lifecycle change is one edit, and the reverse lookup says which
parts it reaches.

    categories.yaml              the category codes an IPN may start with
    mpns/<MPN>.yaml              one manufacturer part
    parts/<category>/<IPN>.yaml  one internal part
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.library.issues import Issue

IPN_PATTERN = re.compile(r"^(?P<category>[A-Za-z0-9]+)-(?P<number>\d+)$")

PART_STATUSES = ("draft", "approved", "obsolete")
LIFECYCLES = ("active", "nrnd", "obsolete", "unknown")

# Returned for a file that could not be read at all. Distinct from None, which
# is what an empty file parses to and is reported as malformed like any other.
_UNREADABLE: Any = object()


@dataclass(frozen=True)
class Category:
    code: str
    name: str


@dataclass(frozen=True)
class Mpn:
    mpn: str
    manufacturer: str = ""
    description: str = ""
    datasheet: str = ""
    lifecycle: str = "unknown"

    @property
    def datasheet_url(self) -> str | None:
        """The datasheet, if it is something a page can safely link to.

        The value comes out of a repository file. Anything that is not a web
        address -- a `javascript:` URL above all -- is shown as text instead.
        """
        return self.datasheet if self.datasheet.startswith(("https://", "http://")) else None


@dataclass(frozen=True)
class MpnRef:
    """An IPN's mention of a manufacturer part."""

    mpn: str
    preferred: bool = False


@dataclass(frozen=True)
class Part:
    ipn: str
    description: str = ""
    status: str = "draft"
    symbol: str = ""
    footprint: str = ""
    fields: Mapping[str, str] = field(default_factory=dict)
    mpns: tuple[MpnRef, ...] = ()
    path: str = ""  # relative to the repository it came from

    @property
    def category(self) -> str:
        match = IPN_PATTERN.match(self.ipn)
        return match.group("category") if match else ""

    @property
    def preferred_mpn(self) -> str | None:
        """The marked source, or the first listed when none is marked."""
        for ref in self.mpns:
            if ref.preferred:
                return ref.mpn
        return self.mpns[0].mpn if self.mpns else None


@dataclass
class PartsIndex:
    categories: dict[str, Category] = field(default_factory=dict)
    mpns: dict[str, Mpn] = field(default_factory=dict)
    parts: dict[str, Part] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    def parts_using(self, mpn: str) -> list[Part]:
        """Every IPN that lists this manufacturer part, by IPN.

        The question asked when a manufacturer discontinues something.
        """
        return sorted(
            (part for part in self.parts.values() if any(ref.mpn == mpn for ref in part.mpns)),
            key=lambda part: part.ipn,
        )


# -- loading ---------------------------------------------------------------


class _Loader:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.index = PartsIndex()

    def report(self, issue: Issue) -> None:
        self.index.issues.append(issue)

    def read(self, path: Path) -> tuple[Any, str]:
        """A file's parsed content, or _UNREADABLE with the reason recorded."""
        relative = path.relative_to(self.root).as_posix()
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8")), relative
        except yaml.YAMLError as exc:
            self.report(Issue.error("invalid-yaml", str(exc).split("\n")[0], relative))
        except (OSError, UnicodeDecodeError) as exc:
            self.report(Issue.error("unreadable", str(exc), relative))
        return _UNREADABLE, relative

    # -- categories.yaml

    def load_categories(self) -> None:
        path = self.root / "categories.yaml"
        if not path.is_file():
            self.report(
                Issue.warning(
                    "no-categories",
                    "categories.yaml is missing, so IPN category codes cannot be checked",
                )
            )
            return

        data, relative = self.read(path)
        if data is _UNREADABLE:
            return
        if not isinstance(data, list):
            self.report(Issue.error("bad-categories", "expected a list of categories", relative))
            return

        for entry in data:
            if not isinstance(entry, dict) or not entry.get("code"):
                self.report(Issue.error("bad-category", f"malformed entry: {entry!r}", relative))
                continue
            code = str(entry["code"])
            self.index.categories[code] = Category(code, str(entry.get("name") or code))

    # -- mpns/

    def load_mpns(self) -> None:
        for path in sorted((self.root / "mpns").glob("*.yaml")):
            data, relative = self.read(path)
            if data is _UNREADABLE:
                continue
            if not isinstance(data, dict) or not data.get("mpn"):
                self.report(Issue.error("bad-mpn", "missing 'mpn'", relative))
                continue

            mpn = str(data["mpn"])
            if mpn != path.stem:
                self.report(
                    Issue.warning(
                        "name-mismatch", f"mpn {mpn!r} is in file {path.stem!r}", relative
                    )
                )

            lifecycle = str(data.get("lifecycle") or "unknown")
            if lifecycle not in LIFECYCLES:
                self.report(
                    Issue.warning(
                        "bad-lifecycle",
                        f"lifecycle {lifecycle!r} is not one of {', '.join(LIFECYCLES)}",
                        relative,
                    )
                )

            if mpn in self.index.mpns:
                self.report(Issue.error("duplicate-mpn", f"{mpn} is already defined", relative))
                continue

            self.index.mpns[mpn] = Mpn(
                mpn=mpn,
                manufacturer=str(data.get("manufacturer") or ""),
                description=str(data.get("description") or ""),
                datasheet=str(data.get("datasheet") or ""),
                lifecycle=lifecycle,
            )

    # -- parts/

    def load_parts(self) -> None:
        parts_dir = self.root / "parts"
        for path in sorted(parts_dir.rglob("*.yaml")):
            data, relative = self.read(path)
            if data is _UNREADABLE:
                continue
            if not isinstance(data, dict) or not data.get("ipn"):
                self.report(Issue.error("bad-part", "missing 'ipn'", relative))
                continue

            ipn = str(data["ipn"])
            self._check_number(ipn, path, parts_dir, relative)

            status = str(data.get("status") or "draft")
            if status not in PART_STATUSES:
                self.report(
                    Issue.warning(
                        "bad-status",
                        f"status {status!r} is not one of {', '.join(PART_STATUSES)}",
                        relative,
                    )
                )

            part = Part(
                ipn=ipn,
                description=str(data.get("description") or ""),
                status=status,
                symbol=str(data.get("symbol") or ""),
                footprint=str(data.get("footprint") or ""),
                fields=self._fields(data.get("fields"), relative),
                mpns=self._mpn_refs(data.get("mpns"), relative),
                path=relative,
            )

            if ipn in self.index.parts:
                self.report(Issue.error("duplicate-ipn", f"{ipn} is already defined", relative))
                continue
            self.index.parts[ipn] = part

    def _check_number(self, ipn: str, path: Path, parts_dir: Path, relative: str) -> None:
        if ipn != path.stem:
            self.report(
                Issue.error("name-mismatch", f"ipn {ipn!r} is in file {path.stem!r}", relative)
            )

        match = IPN_PATTERN.match(ipn)
        if match is None:
            self.report(Issue.error("bad-ipn", f"{ipn!r} is not <category>-<number>", relative))
            return

        category = match.group("category")
        if self.index.categories and category not in self.index.categories:
            self.report(
                Issue.error(
                    "unknown-category",
                    f"category {category!r} is not declared in categories.yaml",
                    relative,
                )
            )
        # parts/1102/1102-0001.yaml keeps a few thousand files browsable.
        if path.parent != parts_dir and path.parent.name != category:
            self.report(
                Issue.warning(
                    "misfiled",
                    f"{ipn} is under {path.parent.name!r}, expected {category!r}",
                    relative,
                )
            )

    def _fields(self, raw: Any, relative: str) -> dict[str, str]:
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            self.report(Issue.error("bad-fields", "'fields' must be a mapping", relative))
            return {}
        return {str(key): str(value) for key, value in raw.items()}

    def _mpn_refs(self, raw: Any, relative: str) -> tuple[MpnRef, ...]:
        if raw is None:
            return ()
        if not isinstance(raw, list):
            self.report(Issue.error("bad-mpns", "'mpns' must be a list", relative))
            return ()

        refs: list[MpnRef] = []
        for entry in raw:
            if isinstance(entry, str):  # the shorthand, for a single source
                refs.append(MpnRef(entry))
            elif isinstance(entry, dict) and entry.get("mpn"):
                refs.append(MpnRef(str(entry["mpn"]), bool(entry.get("preferred"))))
            else:
                self.report(Issue.error("bad-mpn-ref", f"malformed entry: {entry!r}", relative))

        if sum(ref.preferred for ref in refs) > 1:
            self.report(
                Issue.error("multiple-preferred", "more than one MPN is marked preferred", relative)
            )
        return tuple(refs)

    # -- what only shows once everything is loaded

    def check(self) -> None:
        mpns = self.index.mpns
        for part in self.index.parts.values():
            if not part.symbol:
                self.report(Issue.error("no-symbol", f"{part.ipn} names no symbol", part.path))
            if not part.description:
                self.report(
                    Issue.warning(
                        "no-description",
                        f"{part.ipn} has no description; the number alone is unsearchable",
                        part.path,
                    )
                )

            approved = part.status == "approved"
            if approved and not part.mpns:
                self.report(
                    Issue.warning(
                        "no-mpns",
                        f"{part.ipn} is approved but lists no manufacturer part",
                        part.path,
                    )
                )

            for ref in part.mpns:
                if ref.mpn not in mpns:
                    self.report(
                        Issue.error(
                            "unknown-mpn",
                            f"{part.ipn} lists {ref.mpn!r}, which has no file in mpns/",
                            part.path,
                        )
                    )
                elif approved and mpns[ref.mpn].lifecycle == "obsolete":
                    self.report(
                        Issue.warning(
                            "obsolete-mpn",
                            f"{part.ipn} is approved but {ref.mpn} is obsolete",
                            part.path,
                        )
                    )


def has_parts(root: Path) -> bool:
    """Whether a repository carries part definitions at all."""
    return (root / "parts").is_dir()


def load_parts(root: Path) -> PartsIndex:
    """Load categories, MPNs and IPNs, checking everything internal to them.

    Whether each part's symbol and footprint exist is checked later, against
    every source at once: the assets may live in another repository.
    """
    loader = _Loader(root)
    loader.load_categories()
    loader.load_mpns()
    loader.load_parts()
    loader.check()
    return loader.index
