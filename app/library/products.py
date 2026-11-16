"""The products a company makes, and the repository each one is designed in.

    products/<family>.yaml      one product family, and the devices in it

    name: AKD Servodrive
    description: Digital servo drives
    repository: https://github.com/acme/akd      # optional: what the whole family shares
    tags: [servo, drive]
    devices:
      - id: mv-3
        name: AKD MV 3
        repository: https://github.com/acme/akd-mv-3
        tags: [mv, 3A]

A family is a file and a device is an entry in it. The file's name is the
family's id; a device's id only has to be its own within the family. The pair,
`akd/mv-3`, is the key a BOM is attached to.

This describes products. It holds no BOM: a BOM is a record of what was
uploaded, and lives in the database next to the others. Nothing here is
needed for a BOM to be stored -- a device that is later taken out of the tree
leaves its BOMs where they are, listed as belonging to nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.library import files
from app.library.issues import Issue

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

MAX_TAG = 40


def web_address(text: str) -> str | None:
    """The text, if a page can safely link to it.

    It comes out of a repository file. Anything that is not a web address --
    a `javascript:` URL above all -- is shown as text instead.
    """
    return text if text.startswith(("https://", "http://")) else None


@dataclass(frozen=True)
class Device:
    id: str
    family: str
    name: str
    description: str = ""
    repository: str = ""
    tags: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.family}/{self.id}"

    @property
    def repository_url(self) -> str | None:
        return web_address(self.repository)


@dataclass(frozen=True)
class Family:
    id: str
    name: str
    description: str = ""
    repository: str = ""
    tags: tuple[str, ...] = ()
    devices: tuple[Device, ...] = ()
    path: str = ""

    @property
    def repository_url(self) -> str | None:
        return web_address(self.repository)

    @property
    def all_tags(self) -> tuple[str, ...]:
        """The family's own tags and those of its devices: what a filter on a
        tag should find the family by."""
        found = dict.fromkeys(self.tags)
        for device in self.devices:
            found.update(dict.fromkeys(device.tags))
        return tuple(found)


@dataclass(frozen=True)
class Products:
    families: tuple[Family, ...] = ()

    def family(self, family_id: str) -> Family | None:
        return next((family for family in self.families if family.id == family_id), None)

    def device(self, key: str) -> Device | None:
        family_id, _, device_id = key.partition("/")
        family = self.family(family_id)
        if family is None:
            return None
        return next((device for device in family.devices if device.id == device_id), None)

    @property
    def devices(self) -> tuple[Device, ...]:
        return tuple(device for family in self.families for device in family.devices)

    @property
    def tags(self) -> tuple[str, ...]:
        """Every tag in use, in alphabetical order, for a filter to offer.

        Tags that differ only in case are one tag, spelled the way it was
        first written: a filter offering both would be a puzzle.
        """
        found: dict[str, str] = {}
        for family in self.families:
            for tag in family.all_tags:
                found.setdefault(tag.casefold(), tag)
        return tuple(sorted(found.values(), key=str.casefold))

    def __bool__(self) -> bool:
        return bool(self.families)


# -- loading ----------------------------------------------------------------


def _text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    return " ".join(str(value).split()) if value is not None else ""


def _tags(raw: Any, relative: str, owner: str, issues: list[Issue]) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):  # `tags: servo` is a mistake worth forgiving
        raw = [raw]
    if not isinstance(raw, list):
        issues.append(Issue.error("bad-product", f"{owner}: 'tags' must be a list", relative))
        return ()

    tags: dict[str, None] = {}
    for tag in raw:
        text = " ".join(str(tag).split())
        if not text:
            continue
        if len(text) > MAX_TAG:
            issues.append(
                Issue.warning("bad-product", f"{owner}: tag {text[:20]!r}… is too long", relative)
            )
            continue
        tags.setdefault(text)
    return tuple(tags)


def _check_repository(text: str, relative: str, owner: str, issues: list[Issue]) -> None:
    if text and web_address(text) is None:
        issues.append(
            Issue.warning(
                "repository-not-a-link",
                f"{owner}: the repository {text!r} is not a web address, "
                "so it is shown but not linked",
                relative,
            )
        )


def _devices(raw: Any, family: str, relative: str, issues: list[Issue]) -> tuple[Device, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        issues.append(Issue.error("bad-product", f"{family}: 'devices' must be a list", relative))
        return ()

    devices: dict[str, Device] = {}
    for entry in raw:
        if not isinstance(entry, dict) or entry.get("id") is None:
            issues.append(
                Issue.error("bad-product", f"{family}: malformed device: {entry!r}", relative)
            )
            continue

        device_id = str(entry["id"])
        owner = f"{family}/{device_id}"
        if not _ID.match(device_id):
            issues.append(
                Issue.error(
                    "bad-product",
                    f"{owner}: a device id is letters, digits, '.', '_' and '-' only",
                    relative,
                )
            )
            continue
        if device_id in devices:
            issues.append(Issue.error("bad-product", f"{owner} is listed twice", relative))
            continue

        repository = _text(entry, "repository")
        _check_repository(repository, relative, owner, issues)
        devices[device_id] = Device(
            id=device_id,
            family=family,
            name=_text(entry, "name") or device_id,
            description=_text(entry, "description"),
            repository=repository,
            tags=_tags(entry.get("tags"), relative, owner, issues),
        )
    return tuple(devices.values())


def load_products(root: Path) -> tuple[Products, list[Issue]]:
    """Read every family file, and say what was wrong with the ones that were."""
    issues: list[Issue] = []
    families: list[Family] = []

    for path in files.product_files(root):
        relative = files.relative(root, path)
        try:
            data = files.read(path)
        except files.Unreadable as exc:
            issues.append(Issue.error(exc.kind, str(exc), relative))
            continue

        family = path.stem
        if not isinstance(data, dict):
            issues.append(Issue.error("bad-product", "expected a mapping", relative))
            continue
        if not _ID.match(family):
            issues.append(
                Issue.error(
                    "bad-product",
                    f"{path.name}: a family's file name is its id: "
                    "letters, digits, '.', '_' and '-'",
                    relative,
                )
            )
            continue

        repository = _text(data, "repository")
        _check_repository(repository, relative, family, issues)
        families.append(
            Family(
                id=family,
                name=_text(data, "name") or family,
                description=_text(data, "description"),
                repository=repository,
                tags=_tags(data.get("tags"), relative, family, issues),
                devices=_devices(data.get("devices"), family, relative, issues),
                path=relative,
            )
        )

    families.sort(key=lambda family: (family.name.casefold(), family.id))
    return Products(tuple(families)), issues
