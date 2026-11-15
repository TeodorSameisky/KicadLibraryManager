"""The product tree with each device's BOMs under it.

The tree is in git and the BOMs are in the database, and either can change
without the other: a device is renamed or taken out, a BOM is uploaded for a
device nobody has written down yet. So the two are joined here, when a page is
built, and what belongs to nothing is shown as that rather than lost.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.boms.store import Bom
from app.library.products import Device, Family, Products


@dataclass(frozen=True)
class DeviceNode:
    device: Device
    boms: tuple[Bom, ...] = ()  # newest first

    @property
    def latest(self) -> Bom | None:
        return self.boms[0] if self.boms else None


@dataclass(frozen=True)
class FamilyNode:
    family: Family
    devices: tuple[DeviceNode, ...] = ()

    @property
    def bom_count(self) -> int:
        return sum(len(node.boms) for node in self.devices)


@dataclass(frozen=True)
class ProductTree:
    families: tuple[FamilyNode, ...] = ()
    # BOMs that are on no device in the tree: never attached to one, or
    # attached to one that has since gone.
    unassigned: tuple[Bom, ...] = ()
    tags: tuple[str, ...] = ()

    @property
    def device_count(self) -> int:
        return sum(len(node.devices) for node in self.families)

    @property
    def empty(self) -> bool:
        return not self.families


def _newest_first(boms: Iterable[Bom]) -> tuple[Bom, ...]:
    return tuple(sorted(boms, key=lambda bom: (bom.uploaded_at, bom.id), reverse=True))


def build_tree(products: Products, boms: Iterable[Bom]) -> ProductTree:
    by_device: dict[str, list[Bom]] = {}
    for bom in boms:
        by_device.setdefault(bom.device, []).append(bom)

    known = {device.key for device in products.devices}
    unassigned = [bom for key, found in by_device.items() if key not in known for bom in found]

    return ProductTree(
        families=tuple(
            FamilyNode(
                family=family,
                devices=tuple(
                    DeviceNode(device, _newest_first(by_device.get(device.key, ())))
                    for device in family.devices
                ),
            )
            for family in products.families
        ),
        unassigned=_newest_first(unassigned),
        tags=products.tags,
    )
