"""Shared graph invariants for the field-eval corpora.

The v0.24.0 evaluation (bd ...d76r1) found nine defects that the v0.23.1
corpus had run straight past, and the post-mortem named one cause: the corpus
asserted **happy paths** ("both consumers emit an edge") where it should have
asserted **invariants** ("every edge endpoint is a node someone can reach").
A happy-path assertion passes on a graph whose every cross-repo edge dangles,
which is exactly the graph weld was shipping.

These are the invariants, in one importable place so a per-finding regression
test and the end-to-end corpus check the same thing rather than two things
that merely sound alike. They are plain functions raising ``AssertionError``,
not ``TestCase`` methods -- so a probe can call them, and so can a future
gate script.

Graph payloads are accepted in either on-wire shape (``nodes`` as a dict keyed
by id or as a list of node objects; ``edges`` likewise), because the evaluator
probes read ``.weld/graph.json`` straight off disk and both shapes are legal
there.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from weld._discover_external_package_purge import emptied_placeholder_node_ids
from weld.tests._contract_markers import (
    cannot_answer_markers,
    remediation_markers,
)
from weld.workspace import UNIT_SEPARATOR

#: Child lifecycle states that mean "this child is checked out on disk".
#: ``fresh``/``stale`` are what ``weld._federation_staleness`` actually emits;
#: ``present`` is the ``wd workspace status`` spelling of the same condition.
#: Finding N5 is a renderer that counted a state nothing emits.
ON_DISK_CHILD_STATES = frozenset({"fresh", "stale", "present"})

#: Everything else: registered but not answerable from.
ABSENT_CHILD_STATES = frozenset({"missing", "uninitialized", "corrupt", "unknown"})

_ROSTER_RE = re.compile(
    r"children:\s*(?P<registered>\d+)\s+registered,\s*"
    r"(?P<present>\d+)\s+present,\s*(?P<stale>\d+)\s+stale"
    r"(?:\s*\((?P<absent>[^)]*)\))?"
)

#: Substrings that mark output as a cannot-answer outcome (ADR 0134 §2),
#: derived from ``weld/_errors.py`` and not restated here -- these used to be
#: four literals, which made this module a second source of truth for a
#: vocabulary the contract already owns. See :mod:`weld.tests._contract_markers`
#: for what each marker is cut from and why the set is three rather than four.
_CANNOT_ANSWER_MARKERS = cannot_answer_markers()

#: A cannot-answer outcome must also tell the reader what to do about it.
#: Derived from ``weld/_errors.py`` as well (bd ``5038-hkb8x``), and for the
#: same reason as the set above: these were four literals, three of which the
#: contract produces and the fourth of which (``"See "``) it does not. See
#: :mod:`weld.tests._contract_markers` for what each is cut from and where the
#: dropped one went.
_REMEDIATION_MARKERS = remediation_markers()


def graph_nodes(payload: Any) -> dict[str, dict]:
    """Return ``{node_id: node}`` for either on-wire node shape."""
    nodes = payload.get("nodes") if isinstance(payload, Mapping) else None
    if isinstance(nodes, Mapping):
        return {str(k): (v if isinstance(v, Mapping) else {}) for k, v in nodes.items()}
    if isinstance(nodes, list):
        return {str(n.get("id")): n for n in nodes if isinstance(n, Mapping)}
    return {}


def graph_edges(payload: Any) -> list[dict]:
    """Return the edge list for either on-wire edge shape."""
    edges = payload.get("edges") if isinstance(payload, Mapping) else None
    if isinstance(edges, Mapping):
        edges = list(edges.values())
    if not isinstance(edges, list):
        return []
    return [e for e in edges if isinstance(e, Mapping)]


def _describe_edge(edge: Mapping) -> str:
    props = edge.get("props") if isinstance(edge.get("props"), Mapping) else {}
    via = props.get("source_strategy") or props.get("package") or edge.get("type")
    return f"{edge.get('from')!r} -> {edge.get('to')!r} ({via})"


def _classify(
    endpoint: Any,
    root_ids: set[str],
    child_ids: Mapping[str, set[str] | None],
) -> str | None:
    """Return a failure reason for *endpoint*, or ``None`` when it resolves."""
    text = str(endpoint)
    if text in root_ids:
        return None
    if UNIT_SEPARATOR not in text:
        return "no such root node"
    child, _, local = text.partition(UNIT_SEPARATOR)
    if not child or not local:
        return "malformed federated id"
    if child not in child_ids:
        return f"child {child!r} is not registered"
    ids = child_ids[child]
    if ids is None:
        return f"child {child!r} could not be read"
    if local not in ids:
        return f"no node {local!r} in child {child!r}"
    return None


def assert_edges_resolve(
    root_graph: Any,
    child_graphs: Mapping[str, Any],
) -> None:
    """Every edge endpoint in *root_graph* must name a node that exists.

    A federated root may spell an endpoint two ways (ADR 0011 §7): a plain
    root node id (``repo:<child>``), or ``<child>\\x1f<child-local-id>``, which
    resolves into that child's own graph. Anything else is a dangling edge --
    a claim about a relationship between things the reader cannot look up.

    *child_graphs* maps child name to that child's graph payload; a value of
    ``None`` records a registered child whose graph could not be read, which
    makes its endpoints unverifiable rather than merely absent. Both fail:
    per ADR 0134 an unverifiable claim is not an answer.
    """
    root_ids = set(graph_nodes(root_graph))
    child_ids: dict[str, set[str] | None] = {
        name: (None if payload is None else set(graph_nodes(payload)))
        for name, payload in child_graphs.items()
    }

    failures: list[str] = []
    for edge in graph_edges(root_graph):
        for side in ("from", "to"):
            reason = _classify(edge.get(side), root_ids, child_ids)
            if reason is not None:
                failures.append(f"  {_describe_edge(edge)}: {side} {reason}")

    if failures:
        raise AssertionError(
            f"{len(failures)} dangling edge endpoint(s) in the root graph "
            f"({len(root_ids)} root nodes, children: "
            f"{sorted(child_ids)}):\n" + "\n".join(sorted(set(failures)))
        )


def _module_suffixes(node_id: str) -> set[str]:
    """Dotted module paths a first-party node answers to.

    ``file:src/acme_notify/config`` is importable as ``acme_notify.config``
    from ``src/`` and as ``src.acme_notify.config`` from the repo root, so
    every suffix of the path is a name that must resolve first-party. Symbol
    ids (``symbol:py:<dotted module>:<name>``) carry the same module already
    dotted, in their second-to-last field.
    """
    if node_id.startswith("file:"):
        parts = [p for p in node_id[len("file:"):].replace("\\", "/").split("/") if p]
    elif node_id.startswith("symbol:"):
        fields = node_id.split(":")
        if len(fields) < 4:
            return set()
        parts = [p for p in fields[-2].split(".") if p]
    else:
        return set()
    return {".".join(parts[i:]) for i in range(len(parts))} - {""}


def assert_no_first_party_external(
    graph: Any,
    *,
    id_prefix: str = "package:python:",
) -> None:
    """No external package node may shadow a module this graph already holds.

    Finding N4: ``from acme_notify.config import load_config`` minted
    ``package:python:acme_notify.config.load_config`` with
    ``external=True`` beside the real ``file:src/acme_notify/config``. An
    external node is a statement that the definition lives outside this
    repository, and here it demonstrably does not -- so query returns three
    representations of one function and ranks the definite one second.

    The check is deliberately name-based: an external node is a violation when
    any dotted *prefix* of its name is a module the graph already resolves
    first-party. Prefix, not the whole name, because the minted id appends the
    imported symbol to the module path.
    """
    nodes = graph_nodes(graph)
    first_party: set[str] = set()
    for node_id, node in nodes.items():
        if not node_id.startswith(("file:", "symbol:")):
            continue
        props = node.get("props") if isinstance(node.get("props"), Mapping) else {}
        if props.get("external") is True or props.get("origin") == "external":
            continue  # an external stub cannot vouch for a name being local
        first_party |= _module_suffixes(node_id)

    violations: list[str] = []
    for node_id, node in sorted(nodes.items()):
        if not node_id.startswith(id_prefix):
            continue
        props = node.get("props") if isinstance(node.get("props"), Mapping) else {}
        if props.get("external") is not True:
            continue
        dotted = node_id[len(id_prefix):]
        parts = dotted.split(".")
        hit = next(
            (
                ".".join(parts[: i + 1])
                for i in range(len(parts))
                if ".".join(parts[: i + 1]) in first_party
            ),
            None,
        )
        if hit is not None:
            violations.append(
                f"  {node_id} (external, origin={props.get('origin')!r}) "
                f"shadows first-party module {hit!r}"
            )

    if violations:
        raise AssertionError(
            f"{len(violations)} external package node(s) name a module this "
            "graph already holds first-party:\n" + "\n".join(violations)
        )


def _describe_node(node: Mapping) -> str:
    """Name the props the five purge rules key off, so a failure says which fired."""
    props = node.get("props") if isinstance(node.get("props"), Mapping) else {}
    return (
        f"type={node.get('type')!r} "
        f"source_strategy={props.get('source_strategy')!r} "
        f"authority={props.get('authority')!r} roles={props.get('roles')!r}"
    )


def assert_no_orphan_stubs(graph: Any) -> None:
    """No placeholder node survives the anchor that was its only reason to exist.

    ADR 0139 mechanism 6 (bd 5038-ekohj): a golden
    that froze a defect is indistinguishable from one that asserts intent, which
    is how fabricated externals survived regeneration. The verdict here is
    :func:`weld._discover_external_package_purge.emptied_placeholder_node_ids` --
    the production union :func:`weld.discovery_state.purge_stale_nodes` itself
    calls -- run against the graph as loaded. It is deliberately *not* a sixth
    predicate restated test-side: that union already composes five disjoint
    rules and records the disjointness as intentional ("a plain union, never a
    merge of the underlying logic"), so restating them would create exactly the
    producer/asserter desync ADR 0139 mechanism 4 forbids, and a sixth rule
    added to the union joins this invariant with no edit here.

    What a hit means is precise, and narrower than "this node is wrong": the
    node matches a shape incremental discovery purges, so a graph holding one
    disagrees with the graph incremental would have produced from the same tree.
    Which side of that disagreement is the defect is not decided here.

    A *finished* graph is the precondition. These rules answer "did this
    placeholder's last anchor go away", which is only meaningful once every
    strategy has run and ``close_graph`` has re-minted whatever is still
    referenced -- mid-pipeline, a momentarily-orphaned node is expected.

    A payload that yields no nodes passes, exactly as the sibling assertions
    treat one. That is deliberate (ADR 0139 lets an inapplicable family pass an
    explicit empty input) and it is the trap: a caller whose graph failed to
    load reads as green here, so the caller owns proving its input was real.
    """
    nodes = graph_nodes(graph)
    orphans = emptied_placeholder_node_ids(nodes, graph_edges(graph))
    if orphans:
        raise AssertionError(
            f"{len(orphans)} of {len(nodes)} node(s) match a placeholder shape "
            "the production purge union classifies as emptied. Incremental "
            "discovery drops these, so this graph disagrees with the one "
            "incremental would produce from the same tree:\n"
            # Two deliberate spellings, both about this message surviving a
            # hostile graph: .get rather than [nid], so a KeyError cannot
            # replace the diagnostic with a traceback about the reporter; and
            # !r on the id, as _describe_edge already does on its endpoints,
            # so a control character in a node id is escaped rather than
            # rendered into whatever reads the failure.
            + "\n".join(
                f"  {nid!r} ({_describe_node(nodes.get(nid) or {})})"
                for nid in sorted(orphans)
            )
        )


def assert_roster_matches_json(stale_text: str, stale_json: Mapping) -> None:
    """``wd stale``'s human child roster must agree with its own ``--json``.

    Finding N5: the roster counted a ``present`` state the freshness oracle
    never emits, so four fresh children rendered as "0 present" and one edited
    child as "1 present, 1 stale". ``--json`` was right the whole time, which
    is what makes this checkable without hard-coding either side's arithmetic.
    """
    match = _ROSTER_RE.search(stale_text)
    if match is None:
        raise AssertionError(
            "no child roster line in `wd stale` output; expected "
            "'children: N registered, P present, S stale'. Got:\n" + stale_text
        )

    children = stale_json.get("children")
    if not isinstance(children, list):
        raise AssertionError(f"`wd stale --json` has no children list: {stale_json!r}")
    states = [str(c.get("state")) for c in children if isinstance(c, Mapping)]

    expected_present = sum(1 for s in states if s in ON_DISK_CHILD_STATES)
    expected_stale = sum(1 for s in states if s == "stale")
    actual = (
        int(match.group("registered")),
        int(match.group("present")),
        int(match.group("stale")),
    )
    expected = (len(states), expected_present, expected_stale)
    if actual != expected:
        raise AssertionError(
            "child roster disagrees with `wd stale --json`:\n"
            f"  roster (registered, present, stale) = {actual}\n"
            f"  json   (registered, present, stale) = {expected}\n"
            f"  json states = {sorted(states)}\n"
            f"  roster line = {match.group(0)!r}"
        )

    absent_text = match.group("absent") or ""
    rendered_absent = {
        key.strip(): int(value)
        for key, _, value in (part.partition("=") for part in absent_text.split(","))
        if value.strip().isdigit()
    }
    expected_absent = {
        state: states.count(state)
        for state in sorted(ABSENT_CHILD_STATES)
        if states.count(state)
    }
    if rendered_absent != expected_absent:
        raise AssertionError(
            "absent-child breakdown disagrees with `wd stale --json`:\n"
            f"  roster = {rendered_absent}\n  json   = {expected_absent}"
        )


def assert_cannot_answer(exit_code: int, stderr: str, stdout: str = "") -> None:
    """ADR 0134: a missing precondition exits non-zero and says why.

    "Cannot answer" is the third terminal outcome, distinct from "answered,
    empty". Collapsing it into the second is what let an agent record "no
    dependents" about a graph that structurally could not hold one.
    """
    output = f"{stdout}\n{stderr}"
    if exit_code == 0:
        raise AssertionError(
            "cannot-answer outcome exited 0, which is indistinguishable from "
            f"a real negative answer (ADR 0134). Output:\n{output}"
        )
    if not any(marker in output for marker in _CANNOT_ANSWER_MARKERS):
        raise AssertionError(
            "non-zero exit with no cannot-answer marker "
            f"({', '.join(_CANNOT_ANSWER_MARKERS)}). Output:\n{output}"
        )
    if not any(marker in output for marker in _REMEDIATION_MARKERS):
        raise AssertionError(
            "cannot-answer output states no remediation "
            f"({', '.join(_REMEDIATION_MARKERS)}). Output:\n{output}"
        )


def assert_answered_empty(exit_code: int, stdout: str, stderr: str = "") -> None:
    """ADR 0134: a real negative answer exits 0 and claims nothing it cannot.

    The mirror of :func:`assert_cannot_answer`, and the reason both exist: a
    test that only pins the failing side would pass on a tool that had started
    refusing to answer questions it can answer.
    """
    if exit_code != 0:
        raise AssertionError(
            f"answered-empty outcome exited {exit_code}:\n{stdout}\n{stderr}"
        )
    output = f"{stdout}\n{stderr}"
    hit = next((m for m in _CANNOT_ANSWER_MARKERS if m in output), None)
    if hit is not None:
        raise AssertionError(
            f"answered-empty output carries the cannot-answer marker {hit!r}:\n"
            f"{output}"
        )
