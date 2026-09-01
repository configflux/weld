"""One invariant hook, called at every golden choke point (ADR 0139 mech. 5).

bd 5038-ipa1e. A golden freezes whatever the producer emitted on the day it was
written, which makes "asserts intent" and "froze a defect" the same green tick.
Finding N4 is the worked example: fabricated ``external=True`` package nodes sat
in blast-radius goldens and were faithfully asserted for months, then survived a
regeneration -- because the regen path writes bytes and never asks whether the
bytes mean anything.

So the rule this module enforces is about *placement*, not about a new predicate:
every payload that crosses a golden boundary -- the one read on the compare path
and the one written on the regen path -- goes through
:func:`check_golden_graph`. The predicates themselves stay in
:mod:`weld.tests._graph_invariants`, unchanged and shared with the field-eval
corpus, so the goldens and the corpus check one thing rather than two that merely
sound alike.

Applicability is a **precondition, declared per family** (architect ruling R6),
not a follow-up and not a silent pass. Two shapes exist in this tree today and
each had to be measured, not assumed:

* A *federated* root golden (``examples/05-polyrepo``) spells its cross-repo
  endpoints ``<child>\\x1f<child-local-id>``. Those resolve only against the
  children's own graphs, so :func:`child_graphs_from_repo_nodes` reads them from
  the scratch tree the run just discovered into. Passing ``{}`` there would fail
  a correct golden; declaring edges "open" there would drop the only
  edge-resolution check the shipped goldens can make.
* A *strategy fragment* (the byte-identity goldens) is one ``extract()``'s output,
  not a closed graph: its edges deliberately name nodes a later strategy mints.
  ``edges_close=False`` records that, with a reason the report carries.

The third defence is against vacuity, which is the failure mode a hook like this
invites: ``assert_no_orphan_stubs`` and ``assert_no_first_party_external`` both
pass on a payload that yields no nodes, exactly as their own docstrings say. A
golden handed over as unparsed *text*, or one that failed to load, would sail
through all three assertions green. :func:`check_golden_graph` therefore refuses
a payload holding neither nodes nor edges, and returns a report naming the counts
and every skip, so "this family checked nothing" is a sentence someone can read
rather than an absence nobody can see.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from weld.tests._graph_invariants import (
    assert_edges_resolve,
    assert_no_first_party_external,
    assert_no_orphan_stubs,
    graph_edges,
    graph_nodes,
)

#: Node-id prefix every language package shares (``weld._node_ids.package_id``).
_PACKAGE_PREFIX = "package:"

#: Node-id prefix federation mints for a registered child repo.
_REPO_PREFIX = "repo:"


@dataclass(frozen=True)
class GoldenScope:
    """What a golden family's payloads *are*, decided before the hook runs.

    ``edges_close`` is the one dimension that genuinely varies between the four
    families, and it is not a preference: a strategy fragment's edges reach nodes
    the fragment does not contain, so running :func:`assert_edges_resolve` there
    would report every edge as dangling. ``open_edges_reason`` is mandatory when
    it is false -- an unexplained skip is the thing this module exists to prevent,
    so the constructor refuses one.
    """

    family: str
    edges_close: bool = True
    open_edges_reason: str = ""

    def __post_init__(self) -> None:
        if not self.family:
            raise ValueError("GoldenScope.family must name the family")
        if not self.edges_close and not self.open_edges_reason:
            raise ValueError(
                f"GoldenScope({self.family!r}) declares edges_close=False with no "
                "open_edges_reason. A skipped invariant must say why, or the "
                "report cannot tell a considered exemption from an oversight."
            )
        if self.edges_close and self.open_edges_reason:
            raise ValueError(
                f"GoldenScope({self.family!r}) gives an open_edges_reason while "
                "edges_close=True; the reason would never be reported."
            )


def parse_canonical_golden(text: str, *, label: str) -> dict:
    """Parse a golden held as canonical *text* into the payload to assert on.

    The byte-identity family compares serialised bytes, so its goldens reach the
    hook as strings. Handing a string to :func:`graph_nodes` yields zero nodes
    and zero edges -- every assertion then passes on nothing, which is precisely
    the vacuity ADR 0139 mechanism 5 is aimed at. Parsing here, and failing loudly
    when the text is not a JSON object, is what makes the byte-identity hook mean
    something.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"{label}: golden text is not JSON, so no invariant could be "
            f"evaluated over it: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise AssertionError(
            f"{label}: golden text parsed to {type(payload).__name__}, not a "
            "graph object; the invariants have nothing to read."
        )
    return dict(payload)


def child_graphs_from_repo_nodes(
    root_payload: Any, scratch_root: Path,
) -> dict[str, Any]:
    """Read each child's graph, keyed the way the root graph itself names them.

    The mapping from child name to on-disk location is derived from the root's
    own ``repo:`` nodes (``props.path``), never restated here: federation is the
    producer of both the ``repo:<name>`` id and the ``<name>\\x1f<local>`` edge
    endpoint, so a test that re-spelled the roster could drift from the graph it
    is meant to be checking (ADR 0139 mechanism 1).

    A child whose graph cannot be read maps to ``None`` rather than being
    omitted. :func:`assert_edges_resolve` distinguishes the two: an unregistered
    child and an unreadable one both fail, but only the second says the endpoint
    is *unverifiable*, which is the more useful sentence when a scratch tree was
    built wrong.

    ``props.path`` is repo text, and ADR 0115 is explicit that a graph payload
    carries text nobody vetted. ``Path("/scratch") / "/etc/passwd"`` is
    ``/etc/passwd`` in pathlib -- an absolute or ``..``-bearing path would make
    this read outside the tree it was handed. A path that does not stay under
    *scratch_root* is therefore treated as unreadable, which is also the honest
    verdict: a root claiming its child lives outside the workspace has made a
    claim this function cannot verify.
    """
    children: dict[str, Any] = {}
    root = Path(scratch_root).resolve()
    for node_id, node in graph_nodes(root_payload).items():
        if not node_id.startswith(_REPO_PREFIX):
            continue
        name = node_id[len(_REPO_PREFIX):]
        props = node.get("props") if isinstance(node.get("props"), Mapping) else {}
        rel = props.get("path")
        if not isinstance(rel, str) or not rel:
            children[name] = None
            continue
        try:
            graph_path = (root / rel / ".weld" / "graph.json").resolve()
            graph_path.relative_to(root)
            children[name] = json.loads(graph_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            children[name] = None
    return children


def external_package_prefixes(payload: Any) -> tuple[str, ...]:
    """Every ``package:<language>:`` prefix this payload actually carries.

    Architect ruling R6 asks for a per-language ``id_prefix`` on non-Python
    fixtures rather than the ``package:python:`` default, because a fabricated
    external minted for TypeScript or C# would otherwise slip past a check that
    only ever looks at Python. Deriving the prefixes from the payload answers
    that without guessing a constant: ``weld.graph_closure`` mints external
    packages as ``package:<base_language>:<slug>`` for *whatever* language the
    import came from, so whichever languages a golden holds are the languages
    this returns -- and a language nobody has added yet is covered on the day its
    first node appears.

    The membership test is ``props["external"] is True``, matching the predicate
    :func:`assert_no_first_party_external` scans with, so the report can never
    claim coverage the assertion does not actually give.
    """
    prefixes: set[str] = set()
    for node_id, node in graph_nodes(payload).items():
        if not node_id.startswith(_PACKAGE_PREFIX):
            continue
        props = node.get("props") if isinstance(node.get("props"), Mapping) else {}
        if props.get("external") is not True:
            continue
        fields = node_id.split(":")
        prefixes.add(":".join(fields[:2]) + ":" if len(fields) >= 3 else _PACKAGE_PREFIX)
    return tuple(sorted(prefixes))


def check_golden_graph(
    payload: Any,
    *,
    scope: GoldenScope,
    label: str,
    child_graphs: Mapping[str, Any] | None = None,
) -> str:
    """Run every applicable graph invariant over one golden-boundary payload.

    Call this on both sides of every golden: the payload read on the compare path
    and the payload written on the regen path. Hooking only the compare side is
    how finding N4's fabricated externals survived -- the regen path rewrote the
    golden from a graph nothing had checked, and the next compare dutifully
    asserted the new bytes.

    *child_graphs* is required rather than defaulted so a single-repo family says
    ``{}`` out loud (R6). ``None`` is rejected: it is the shape a caller lands on
    by forgetting, and a forgotten child roster silently turns every federated
    endpoint into a dangling one.

    Returns a one-line report naming the counts, the invariants that ran, and the
    reason behind any that did not, so a caller or a reviewer can see what a
    family actually checked.
    """
    if child_graphs is None:
        raise AssertionError(
            f"{label}: check_golden_graph needs an explicit child_graphs mapping; "
            "pass {} for a single-repo golden (ADR 0139 R6) or the children read "
            "from the scratch tree for a federated one."
        )

    nodes = graph_nodes(payload)
    edges = graph_edges(payload)
    if not nodes and not edges:
        raise AssertionError(
            f"{label}: payload yields no nodes and no edges, so all three "
            "invariants would pass on nothing. Either the golden failed to load, "
            "or canonical text reached the hook unparsed -- see "
            "parse_canonical_golden."
        )

    notes = [f"{label}: {len(nodes)} nodes, {len(edges)} edges"]

    if scope.edges_close:
        assert_edges_resolve(payload, child_graphs)
        # Child names and package prefixes are cut from node ids, which are repo
        # text (ADR 0115). Rendered through the list repr rather than joined raw,
        # so a control character in one escapes instead of reaching whatever
        # prints this -- the same treatment assert_edges_resolve already gives
        # its own roster.
        notes.append(f"edges_resolve over {sorted(child_graphs)}")
    else:
        notes.append(f"edges_resolve SKIPPED -- {scope.open_edges_reason}")

    prefixes = external_package_prefixes(payload)
    if prefixes:
        for prefix in prefixes:
            assert_no_first_party_external(payload, id_prefix=prefix)
        notes.append(f"no_first_party_external over {list(prefixes)}")
    else:
        # Not a skip: nothing can shadow a first-party module when the payload
        # mints no external package at all. Reported anyway, because "passed"
        # and "had nothing to look at" are the two readings R6 refuses to leave
        # indistinguishable -- and today every shipped golden is the second.
        notes.append("no_first_party_external vacuous -- no external package node")

    assert_no_orphan_stubs(payload)
    notes.append("no_orphan_stubs")
    return "; ".join(notes)
