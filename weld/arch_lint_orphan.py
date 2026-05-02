"""Orphan-detection rule with default node-type suppression.

Default behaviour suppresses orphan reports for ``doc``, ``config``, and
``test`` node types because:

* A doc/config file with no incoming or outgoing edges in the graph is
  almost always a true intentional leaf (a README, a CI config), not
  dead code.
* A test file/symbol with no graph edges is almost always a sibling test
  whose discovery does not yet emit an edge to the system-under-test.

These categories overwhelmed the signal of true symbol orphans (the
actual dead-code candidates).  Pass ``include_noisy=True`` to restore
the broad sweep.

A node is considered "test" when its file path matches one of:

* contains ``_test.py`` / ``_test.go`` / ``.test.ts`` / ``.test.tsx``
* lives in a directory segment named ``tests`` / ``__tests__``
* file basename starts with ``test_``

The rule yields a tuple ``(violations, suppressed_count)``: the runner
attaches ``suppressed_count`` to the lint envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from weld.arch_lint import Violation

# Node types whose orphans are almost always intentional leaves rather
# than dead code.  Kept centralised so the runner and the formatter
# stay in sync.
_DEFAULT_SUPPRESSED_TYPES: frozenset[str] = frozenset(
    {"doc", "config"}
)


def _looks_like_test_path(path: str) -> bool:
    """Return True when *path* looks like a test source file."""
    if not path:
        return False
    p = path.replace("\\", "/").lower()
    if "_test.py" in p or "_test.go" in p:
        return True
    if ".test.ts" in p or ".test.tsx" in p or ".test.js" in p:
        return True
    segments = p.split("/")
    if "tests" in segments or "__tests__" in segments:
        return True
    base = segments[-1]
    if base.startswith("test_"):
        return True
    return False


def _is_test_node(node: dict) -> bool:
    """Return True when *node* should be classified as a test node."""
    props = node.get("props") or {}
    file_path = str(props.get("file") or "")
    if _looks_like_test_path(file_path):
        return True
    # Roles tagged 'test' (rare but possible).
    roles = props.get("roles") or []
    if isinstance(roles, list) and "test" in roles:
        return True
    return False


def _is_suppressed(node: dict) -> bool:
    """Return True when *node* falls into the default-suppression set."""
    node_type = node.get("type") or ""
    if node_type in _DEFAULT_SUPPRESSED_TYPES:
        return True
    if _is_test_node(node):
        return True
    return False


def _make_violation(node_id: str, label: str) -> "Violation":
    from weld.arch_lint import Violation  # late import to break cycle

    return Violation(
        rule="orphan-detection",
        node_id=node_id,
        message=(
            f"node {node_id!r} ({label}) has no incoming or outgoing "
            f"edges; likely dead code or a discovery gap"
        ),
    )


def detect_orphans(
    data: dict, *, include_noisy: bool = False
) -> tuple[list["Violation"], int]:
    """Return (violations, suppressed_count) for the orphan-detection rule.

    ``data`` is the dict returned by ``Graph.dump()``.  When
    ``include_noisy=False`` (the default) violations on suppressed
    node types are dropped from the visible list and counted in
    ``suppressed_count``.  When ``include_noisy=True`` every orphan is
    reported and ``suppressed_count`` is ``0``.
    """
    nodes: dict = data.get("nodes", {}) or {}
    edges: list = data.get("edges", []) or []

    touched: set[str] = set()
    for edge in edges:
        frm = edge.get("from")
        to = edge.get("to")
        if isinstance(frm, str):
            touched.add(frm)
        if isinstance(to, str):
            touched.add(to)

    violations: list["Violation"] = []
    suppressed = 0
    for node_id in sorted(node_id for node_id in nodes if node_id not in touched):
        node = nodes.get(node_id) or {}
        label = node.get("label") or node_id
        if not include_noisy and _is_suppressed(node):
            suppressed += 1
            continue
        violations.append(_make_violation(node_id, label))
    return violations, suppressed


def rule_orphan_detection(data: dict) -> Iterable["Violation"]:
    """Backwards-compatible entrypoint -- yields visible orphans only.

    The runner uses ``detect_orphans`` directly so it can capture the
    suppressed count; this function is kept for any caller that imports
    the rule the same way other rules are imported.
    """
    violations, _ = detect_orphans(data, include_noisy=False)
    yield from violations
