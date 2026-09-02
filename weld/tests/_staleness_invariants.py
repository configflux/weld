"""The invariant a freshness verdict has to satisfy: it names what it blames.

ADR 0141 D1, from field-eval finding M1. Wiring ``cross_repo_strategies:
[package_graph]`` at a federation root made ``wd stale`` report ``stale: yes``
with an **empty** ``stale_sources``, permanently -- every child fresh, the
graph sha equal to the current sha, nothing behind, no coverage doubt, and
``wd discover`` (the remedy the message names) unable to clear it. ``wd
impact`` then refused to answer without ``--allow-stale``. The gate condemned
on exactly the state its own detail declines to name, which is ADR 0134's "a
verdict nobody can act on" in its staleness instance.

So the contract here is not "staleness is computed correctly" -- that is the
fix's job, and no test states it in one line. It is the weaker, checkable
thing a user actually needs: **a stale verdict carries a basis**. If the tool
says the graph is stale, the same payload must say, somewhere a reader can
find it, what made it so.

Deliberately *not* spelled as "``stale_sources`` is non-empty", which would
fail correct behaviour. :mod:`weld._stale_reasons` documents three states that
leave that list empty on purpose rather than inventing a path to blame -- no
recorded ``git_sha``, unreachable history (``commits_behind == -1``), and the
ADR 0101 doubt with no specific uncovered file -- and says they "stay
distinguished by the existing top-level ``reason`` / ``graph_sha`` /
``commits_behind`` fields". Those fields are therefore a basis, and this
module reads them as one. What M1 has is none of the alternatives either: the
basis vocabulary below is the union of everything the product itself offers,
and the finding is the payload that offers nothing from it.

The reason strings are imported from the product rather than restated, so a
fix that satisfies the letter by inventing a fifth reason fails here instead
of passing (ADR 0139 mechanism 4, the shape ``_contract_markers`` uses).

This lives beside :mod:`weld.tests._graph_invariants` rather than in it only
because that module is at its line cap; it is the same kind of check --
``assert_roster_matches_json`` reads the same ``wd stale --json`` payload --
and rides in the same ``:graph_invariants_lib``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from weld._stale_reasons import ALL_REASONS

#: Child lifecycle states that mean "this child is not a reason to be stale".
#: Mirrors :data:`weld.tests._graph_invariants.ON_DISK_CHILD_STATES` minus
#: ``stale``, which is precisely the state that *is* a reason.
_FRESH_CHILD_STATES = frozenset({"fresh", "present"})


def _children(payload: Mapping) -> list[Mapping]:
    children = payload.get("children")
    if not isinstance(children, list):
        return []
    return [child for child in children if isinstance(child, Mapping)]


def stale_verdict_basis(payload: Any) -> list[str]:
    """Every basis the payload offers for calling itself stale, as text.

    Read off the payload rather than restated: each entry quotes the field
    that carries it, so a failure message names what a reader would have had
    to act on. An empty list is the violation
    :func:`assert_stale_verdict_names_its_basis` reports.
    """
    if not isinstance(payload, Mapping):
        return []
    basis: list[str] = []

    sources = payload.get("stale_sources")
    if isinstance(sources, list) and sources:
        basis.append(f"stale_sources={sources[:5]}")
    omitted = payload.get("stale_sources_omitted")
    if isinstance(omitted, int) and omitted > 0:
        basis.append(f"stale_sources_omitted={omitted}")

    # The three no-file-level-detail states weld._stale_reasons documents,
    # each named by the top-level field that module points a reader at.
    reason = payload.get("reason")
    if isinstance(reason, str) and reason.strip():
        basis.append(f"reason={reason!r}")
    if "graph_sha" in payload and payload.get("graph_sha") is None:
        basis.append("graph_sha=None (nothing recorded to compare against)")
    if payload.get("commits_behind") == -1:
        basis.append("commits_behind=-1 (unreachable history)")

    if payload.get("coverage_stale"):
        basis.append("coverage_stale")
    if payload.get("sha_behind") or payload.get("root_sha_behind"):
        basis.append(f"sha_behind (commits_behind={payload.get('commits_behind')})")
    unfresh = [
        f"{child.get('name')}={child.get('state')}"
        for child in _children(payload)
        if str(child.get("state")) not in _FRESH_CHILD_STATES
    ]
    if unfresh:
        basis.append(f"children {unfresh}")
    return basis


def assert_stale_verdict_names_its_basis(payload: Any, *, where: str = "") -> None:
    """A ``wd stale --json`` payload that reports stale must say why.

    Two halves, both ADR 0141 D1:

    * a ``stale: true`` verdict offers at least one basis (see
      :func:`stale_verdict_basis`);
    * whatever it puts in ``stale_sources`` is a real blame -- a path and a
      reason from the closed vocabulary -- so the first half cannot be
      satisfied by minting a placeholder entry, which is the cheapest wrong
      way to make a verdict "name" something.

    A payload that is not stale is not this invariant's business and passes:
    "fresh" needs no justification, and demanding one would make the check
    fail on the very state the fix is trying to reach.
    """
    label = f"{where}: " if where else ""
    if not isinstance(payload, Mapping):
        raise AssertionError(f"{label}not a wd stale --json payload: {payload!r}")

    sources = payload.get("stale_sources")
    if isinstance(sources, list):
        for entry in sources:
            if not isinstance(entry, Mapping):
                raise AssertionError(
                    f"{label}stale_sources entry is not a record: {entry!r}"
                )
            if not str(entry.get("path") or "").strip():
                raise AssertionError(
                    f"{label}stale_sources entry blames no path: {entry!r}"
                )
            if entry.get("reason") not in ALL_REASONS:
                raise AssertionError(
                    f"{label}stale_sources entry carries a reason outside the "
                    f"closed vocabulary weld._stale_reasons defines "
                    f"({sorted(ALL_REASONS)}): {entry!r}"
                )

    if not payload.get("stale"):
        return

    if not stale_verdict_basis(payload):
        raise AssertionError(
            f"{label}`wd stale` reports stale and names nothing that made it "
            f"so -- no stale source, no reason, a recorded graph_sha, history "
            f"reachable, no stale coverage, nothing behind HEAD, no unfresh "
            f"child. A verdict a reader cannot act on (ADR 0134, ADR 0141 D1)."
            f" Payload: {dict(payload)}"
        )
