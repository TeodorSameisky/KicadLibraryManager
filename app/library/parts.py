"""Reading the parts out of a library's repository.

What a part is belongs to `app.domain`, and where the files are and how they
are written down belongs to `app.library.files`. This is what lies between:
what each file says is held to what such a file has to say, and gathered into
one index, with everything that was wrong reported and nothing given up on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.domain import rules
from app.domain.parts import IPN_PATTERN, Category, Lifecycle, Mpn, MpnRef, Part, PartStatus
from app.domain.rules import Finding, Rule
from app.domain.templates import Template, TemplateField
from app.library import files
from app.library.issues import Issue, Severity

# Returned for a file that could not be read at all. Distinct from None, which
# is what an empty file parses to and is reported as malformed like any other.
_UNREADABLE: Any = object()


@dataclass
class PartsIndex:
    categories: dict[str, Category] = field(default_factory=dict)
    templates: dict[str, Template] = field(default_factory=dict)  # by category code
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


# -- a part that falls short, as an issue ------------------------------------

# How each rule is weighed and worded for a part that is already in the
# library. Most are errors. The two that are not are the ones a part can fall
# short of and still be placed as it was meant.
_ISSUES: dict[Rule, tuple[Severity, str]] = {
    Rule.NO_DESCRIPTION: (
        Severity.WARNING,
        "{ipn} has no description; the number alone is unsearchable",
    ),
    Rule.NO_SYMBOL: (Severity.ERROR, "{ipn} names no symbol"),
    Rule.UNQUALIFIED_SYMBOL: (Severity.ERROR, "symbol {symbol!r} is not <library>:<name>"),
    Rule.MISSING_SYMBOL: (
        Severity.ERROR,
        "{ipn} names symbol {symbol!r}, which no source provides",
    ),
    # Reported against the symbol too, but this is the line that says which
    # part cannot be placed because of it.
    Rule.UNPLACEABLE: (Severity.ERROR, "{ipn} cannot be placed: {detail}"),
    Rule.UNQUALIFIED_FOOTPRINT: (
        Severity.ERROR,
        "footprint {footprint!r} is not <library>:<name>",
    ),
    Rule.MISSING_FOOTPRINT: (
        Severity.ERROR,
        "{ipn} names footprint {footprint!r}, which no source provides",
    ),
    Rule.TEMPLATE: (Severity.WARNING, "{ipn}: {detail}"),
    Rule.MULTIPLE_PREFERRED: (Severity.ERROR, "more than one MPN is marked preferred"),
}


def part_issue(finding: Finding, part: Part) -> Issue:
    """What a part in the library falls short of, as an entry for the issue list."""
    severity, wording = _ISSUES[finding.rule]
    message = wording.format(
        ipn=part.ipn, symbol=part.symbol, footprint=part.footprint, detail=finding.detail
    )
    return Issue(severity, finding.rule.value, message, part.path)


# -- loading ---------------------------------------------------------------


class _Loader:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.index = PartsIndex()

    def report(self, issue: Issue) -> None:
        self.index.issues.append(issue)

    def read(self, path: Path) -> tuple[Any, str]:
        """A file's parsed content, or _UNREADABLE with the reason recorded."""
        relative = files.relative(self.root, path)
        try:
            return files.read(path), relative
        except files.Unreadable as exc:
            self.report(Issue.error(exc.kind, str(exc), relative))
        return _UNREADABLE, relative

    # -- categories.yaml

    def load_categories(self) -> None:
        path = files.categories_file(self.root)
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

    # -- templates/

    def load_templates(self) -> None:
        for path in files.template_files(self.root):
            data, relative = self.read(path)
            if data is _UNREADABLE:
                continue
            if not isinstance(data, dict):
                self.report(Issue.error("bad-template", "expected a mapping", relative))
                continue

            # The file is named after the category it is for.
            category = path.stem
            stated = data.get("category")
            if stated is not None and str(stated) != category:
                self.report(
                    Issue.warning(
                        "name-mismatch",
                        f"template for category {str(stated)!r} is in file {path.stem!r}; "
                        "the file name is what is used",
                        relative,
                    )
                )
            if self.index.categories and category not in self.index.categories:
                self.report(
                    Issue.error(
                        "unknown-category",
                        f"category {category!r} is not declared in categories.yaml",
                        relative,
                    )
                )

            self.index.templates[category] = Template(
                category=category,
                fields=self._template_fields(data.get("fields"), relative),
                value=str(data.get("value") or ""),
            )

    def _template_fields(self, raw: Any, relative: str) -> tuple[TemplateField, ...]:
        if raw is None:
            return ()
        if not isinstance(raw, list):
            self.report(Issue.error("bad-template", "'fields' must be a list", relative))
            return ()

        fields: list[TemplateField] = []
        for entry in raw:
            if isinstance(entry, str):  # the shorthand: a name, and nothing demanded of it
                entry = {"name": entry}
            if not isinstance(entry, dict) or not entry.get("name"):
                self.report(Issue.error("bad-template", f"malformed field: {entry!r}", relative))
                continue

            options = entry.get("options") or []
            if not isinstance(options, list):
                self.report(
                    Issue.error(
                        "bad-template", f"{entry['name']}: 'options' must be a list", relative
                    )
                )
                options = []

            name = str(entry["name"])
            if any(existing.name == name for existing in fields):
                self.report(
                    Issue.error("bad-template", f"field {name!r} is listed twice", relative)
                )
                continue
            fields.append(
                TemplateField(
                    name=name,
                    required=bool(entry.get("required")),
                    options=tuple(str(option) for option in options),
                    example=str(entry.get("example") or ""),
                    default=str(entry.get("default") or ""),
                )
            )
        return tuple(fields)

    # -- mpns/

    def load_mpns(self) -> None:
        for path in files.mpn_files(self.root):
            data, relative = self.read(path)
            if data is _UNREADABLE:
                continue
            if not isinstance(data, dict) or not data.get("mpn"):
                self.report(Issue.error("bad-mpn", "missing 'mpn'", relative))
                continue

            mpn = str(data["mpn"])
            if files.mpn_file_stem(mpn) != path.stem:
                self.report(
                    Issue.warning(
                        "name-mismatch", f"mpn {mpn!r} is in file {path.stem!r}", relative
                    )
                )

            lifecycle = str(data.get("lifecycle") or Lifecycle.UNKNOWN)
            if lifecycle not in Lifecycle:
                self.report(
                    Issue.warning(
                        "bad-lifecycle",
                        f"lifecycle {lifecycle!r} is not one of {', '.join(Lifecycle)}",
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
        parts_dir = files.parts_directory(self.root)
        for path in files.part_files(self.root):
            data, relative = self.read(path)
            if data is _UNREADABLE:
                continue
            if not isinstance(data, dict) or not data.get("ipn"):
                self.report(Issue.error("bad-part", "missing 'ipn'", relative))
                continue

            ipn = str(data["ipn"])
            self._check_number(ipn, path, parts_dir, relative)

            status = str(data.get("status") or PartStatus.DRAFT)
            if status not in PartStatus:
                self.report(
                    Issue.warning(
                        "bad-status",
                        f"status {status!r} is not one of {', '.join(PartStatus)}",
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
        return tuple(refs)

    # -- what only shows once everything is loaded

    def check(self) -> None:
        mpns = self.index.mpns
        for part in self.index.parts.values():
            # What is asked of any part, of a requested one as much as of this
            # one. Whether its symbol and footprint exist is looked at later,
            # against every source at once.
            for finding in rules.part_findings(
                description=part.description,
                symbol=part.symbol,
                fields=part.fields,
                preferred=[ref.preferred for ref in part.mpns],
                template=self.index.templates.get(part.category),
            ):
                self.report(part_issue(finding, part))

            # What can only be asked of a part that is already in the library:
            # how it stands with the manufacturer parts it lists.
            approved = part.status == PartStatus.APPROVED
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
                elif approved and mpns[ref.mpn].lifecycle == Lifecycle.OBSOLETE:
                    self.report(
                        Issue.warning(
                            "obsolete-mpn",
                            f"{part.ipn} is approved but {ref.mpn} is obsolete",
                            part.path,
                        )
                    )


def load_parts(root: Path) -> PartsIndex:
    """Load categories, MPNs and IPNs, checking everything internal to them.

    Whether each part's symbol and footprint exist is checked later, against
    every source at once: the assets may live in another repository.
    """
    loader = _Loader(root)
    loader.load_categories()
    loader.load_templates()
    loader.load_mpns()
    loader.load_parts()
    loader.check()
    return loader.index
