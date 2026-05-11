"""Internal helpers for :mod:`weld.strategies.csharp_msbuild_targets`.

Pulls the XML walk and the per-target record dataclass out of the
public strategy so the public file stays under the 300-line line-count
cap that ADR 0056 mandates for new strategies. Keeping the
``TargetRecord`` shape here means the consumer (`extract()`) can stay
focused on graph emission.

Edge-case posture:

- Malformed XML returns ``None`` from :func:`parse_msbuild_xml`. The
  public strategy treats that as "no targets" so a single typo does
  not crash discovery.
- An unnamed ``<Target>`` (no ``Name=`` attribute) is skipped by
  :func:`iter_target_records` -- MSBuild itself rejects it, and
  emitting a nameless node would collide with every other unnamed
  target in the same repo.
- MSBuild's legacy ``http://schemas.microsoft.com/developer/msbuild/2003``
  namespace is handled by :func:`_local_name` -- the rest of the
  parser is namespace-agnostic.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class TargetRecord:
    """One ``<Target Name="...">`` declaration in an MSBuild file."""

    name: str
    before_targets: list[str] = field(default_factory=list)
    after_targets: list[str] = field(default_factory=list)
    depends_on_targets: list[str] = field(default_factory=list)
    itemgroup_count: int = 0
    item_count: int = 0


def parse_msbuild_xml(path: Path) -> ET.Element | None:
    """Return the parsed XML root for *path* or ``None`` on any failure.

    MSBuild project files are user-edited XML; tolerating malformed
    input ensures a single typo in a customer project file does not
    kill discovery for the whole repo.
    """
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError, UnicodeDecodeError):
        return None


def iter_target_records(root_elem: ET.Element) -> Iterator[TargetRecord]:
    """Yield one :class:`TargetRecord` per ``<Target>`` under *root_elem*.

    Walks every descendant element. A ``<Target>`` without a ``Name``
    attribute is silently skipped: emitting a nameless target would
    create id collisions and MSBuild itself rejects unnamed targets.
    """
    for elem in root_elem.iter():
        if _local_name(elem.tag) != "Target":
            continue
        name = (elem.attrib.get("Name") or "").strip()
        if not name:
            continue

        before = split_msbuild_list(elem.attrib.get("BeforeTargets", ""))
        after = split_msbuild_list(elem.attrib.get("AfterTargets", ""))
        depends = split_msbuild_list(
            elem.attrib.get("DependsOnTargets", "")
        )

        itemgroup_count, item_count = _count_itemgroups(elem)

        yield TargetRecord(
            name=name,
            before_targets=before,
            after_targets=after,
            depends_on_targets=depends,
            itemgroup_count=itemgroup_count,
            item_count=item_count,
        )


def _count_itemgroups(target_elem: ET.Element) -> tuple[int, int]:
    """Return ``(itemgroup_count, item_count)`` for a ``<Target>``.

    Counts only the direct ``<ItemGroup>`` children of the target.
    Nested item groups (rare but legal inside conditional groups) are
    not traversed: Wave 3 records the surface declaration, not its
    expansion.
    """
    itemgroup_count = 0
    item_count = 0
    for child in target_elem:
        if _local_name(child.tag) != "ItemGroup":
            continue
        itemgroup_count += 1
        item_count += sum(1 for _ in child)
    return itemgroup_count, item_count


def split_msbuild_list(value: str) -> list[str]:
    """Split a semicolon-separated MSBuild attribute value.

    The MSBuild docs call this format a "semicolon-separated list".
    Empty entries (``"Build;;Pack"``) and surrounding whitespace are
    tolerated; the return value is the ordered list of non-empty
    trimmed names. The strategy in
    :mod:`weld.strategies.csharp_msbuild_targets` re-exports this
    function as ``_split_targets_attribute`` so the unit test can
    exercise it through the public-strategy module while the
    canonical implementation stays here (one definition, one rule).
    """
    if not value:
        return []
    return [piece.strip() for piece in value.split(";") if piece.strip()]


def _local_name(tag: object) -> str:
    """Strip an XML namespace from *tag* and return the local name.

    MSBuild emits the legacy
    ``http://schemas.microsoft.com/developer/msbuild/2003`` namespace
    by default for project files authored before SDK-style; stripping
    centrally lets the rest of the walk stay namespace-agnostic.
    """
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


__all__ = [
    "TargetRecord",
    "iter_target_records",
    "parse_msbuild_xml",
    "split_msbuild_list",
]
