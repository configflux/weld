"""Strategy: Runtime contract and verification-surface linkage.

Ingests ``docs/runtime-contract.md`` (or any markdown file glob the source
points at) as the authoritative record of runtime boundaries, health
endpoints, and cross-boundary linkage for the HTTP interaction graph.

Per ADR 0018's static-truth policy this strategy only emits:

- ``rpc`` nodes for healthcheck endpoints declared verbatim in the
  "Runtime Summary" table (``GET /healthz``, ``GET /readyz`` for the
  ``api`` boundary). Each carries full interaction metadata
  (``protocol``, ``surface_kind``, ``transport``, ``boundary_kind``,
  ``declared_in``) so retrieval can surface them alongside FastAPI
  route nodes.
- ``documents`` edges from the runtime-contract doc node to each
  service whose boundary row is present.
- ``exposes`` edges from ``service:api`` to each extracted healthcheck
  rpc node.
- ``verifies`` edges from known gate nodes to the runtime-contract
  doc, so verification surfaces attach to the same slice of the
  graph.
- ``relates_to`` edges from each existing ``deploy`` node back to the
  runtime-contract doc, linking deployment and runtime-contract
  surfaces without re-declaring deploy facts.

The strategy never re-creates nodes produced elsewhere (e.g. the
markdown strategy already emits ``doc:guide/runtime-contract``); it
only emits edges onto those nodes. Edges whose endpoints do not exist
at merge time are dropped by the discover orchestrator, so partial
coverage stays honest.

"""

from __future__ import annotations

import re
from pathlib import Path

from weld.strategies._helpers import StrategyResult, filter_glob_results, should_skip

#: Well-known boundaries from runtime-contract.md that map to service nodes.
_BOUNDARY_TO_SERVICE: dict[str, str] = {
    "web": "service:web",
    "api": "service:api",
    "worker": "service:worker",
}

#: Gate nodes declared in the static topology that verify the runtime
#: contract when the contract doc is present.
_RUNTIME_VERIFICATION_GATES: tuple[str, ...] = (
    "gate:local-verification",
    "gate:run-e2e",
)

#: Regex matching a boundary row in the "Runtime Summary" table. Rows
#: look like ``| \`api\` | ... | ... | \`GET /healthz\`, ... | ... |``.
_BOUNDARY_ROW_RE = re.compile(
    r"^\|\s*`(?P<boundary>[a-zA-Z0-9_-]+)`\s*\|",
    re.MULTILINE,
)

#: Regex matching a healthcheck declaration of the form ``GET /path``
#: or ``POST /path`` inside a markdown table cell. The runtime contract
#: only declares HTTP verbs today.
_HEALTHCHECK_RE = re.compile(
    r"`(?P<method>GET|POST|PUT|DELETE|PATCH|HEAD)\s+(?P<path>/[A-Za-z0-9_/.-]*)`"
)

def _parse_boundaries(text: str) -> list[dict]:
    """Return one dict per row of the ``Runtime Summary`` table.

    Each dict carries the boundary name plus the full row text so the
    caller can pull structured fields (healthchecks etc.) without a
    second pass.
    """
    rows: list[dict] = []
    # Only look inside the Runtime Summary section to avoid stray rows.
    summary_start = text.find("## Runtime Summary")
    if summary_start == -1:
        return rows
    # Stop at the next top-level section.
    summary_end = text.find("\n## ", summary_start + 1)
    summary_text = text[summary_start:summary_end if summary_end != -1 else len(text)]

    for match in _BOUNDARY_ROW_RE.finditer(summary_text):
        boundary = match.group("boundary")
        line_start = summary_text.rfind("\n", 0, match.start()) + 1
        line_end = summary_text.find("\n", match.end())
        if line_end == -1:
            line_end = len(summary_text)
        row_text = summary_text[line_start:line_end]
        rows.append({"boundary": boundary, "row": row_text})
    return rows

def _extract_healthchecks(row: str) -> list[dict]:
    """Return any ``METHOD /path`` pairs declared inside a summary row."""
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for match in _HEALTHCHECK_RE.finditer(row):
        method = match.group("method").upper()
        path = match.group("path")
        key = (method, path)
        if key in seen:
            continue
        seen.add(key)
        found.append({"method": method, "path": path})
    return found

def _slug(path: str) -> str:
    """Derive a stable slug from a URL path for use in node IDs."""
    cleaned = path.strip("/").replace("/", "-")
    return cleaned or "root"

def _make_rpc_node(
    method: str,
    path: str,
    rel_path: str,
) -> tuple[str, dict]:
    """Build a healthcheck ``rpc`` node with full interaction metadata."""
    nid = f"rpc:runtime-contract/{method.lower()}-{_slug(path)}"
    label = f"{method} {path}"
    node = {
        "type": "rpc",
        "label": label,
        "props": {
            "source_strategy": "runtime_contract",
            "authority": "canonical",
            "confidence": "definite",
            "roles": ["doc"],
            "file": rel_path,
            "protocol": "http",
            "surface_kind": "request_response",
            "transport": "http",
            "boundary_kind": "inbound",
            "declared_in": rel_path,
        },
    }
    return nid, node

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract runtime-contract linkage nodes and edges.

    The strategy is a no-op for any file other than runtime-contract
    markdown (checked by content heuristic: the doc must contain a
    ``## Runtime Summary`` heading). Callers can therefore point the
    source glob at a wider markdown tree without false positives.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    excludes = source.get("exclude", [])

    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    if "**" in pattern:
        matched = sorted(root.glob(pattern))
        matched = filter_glob_results(root, matched)
    else:
        parent = (root / pattern).parent
        if not parent.is_dir():
            return StrategyResult(nodes, edges, discovered_from)
        matched = sorted(parent.glob(Path(pattern).name))

    for md_file in matched:
        if not md_file.is_file():
            continue
        if should_skip(md_file, excludes, root=root):
            continue

        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            continue

        if "## Runtime Summary" not in text:
            # Only the runtime-contract doc carries this section; skip
            # anything else the glob dragged in.
            continue

        rel_path = str(md_file.relative_to(root))
        discovered_from.append(rel_path)

        # The markdown strategy already creates this doc node; we only
        # emit edges onto it. The orchestrator drops edges whose
        # endpoints never materialize, so partial runs stay honest.
        doc_id = f"doc:guide/{md_file.stem}"

        boundaries = _parse_boundaries(text)
        seen_services: set[str] = set()
        for row in boundaries:
            boundary = row["boundary"]
            svc_id = _BOUNDARY_TO_SERVICE.get(boundary)
            if svc_id and svc_id not in seen_services:
                seen_services.add(svc_id)
                edges.append({
                    "from": doc_id,
                    "to": svc_id,
                    "type": "documents",
                    "props": {
                        "source_strategy": "runtime_contract",
                        "confidence": "definite",
                    },
                })

            # Only stamp rpc nodes for ``api``-family boundaries: the
            # runtime contract declares HTTP healthchecks only there.
            if boundary != "api":
                continue
            for hc in _extract_healthchecks(row["row"]):
                nid, node = _make_rpc_node(hc["method"], hc["path"], rel_path)
                nodes[nid] = node
                edges.append({
                    "from": svc_id or "service:api",
                    "to": nid,
                    "type": "exposes",
                    "props": {
                        "source_strategy": "runtime_contract",
                        "confidence": "definite",
                    },
                })
                edges.append({
                    "from": doc_id,
                    "to": nid,
                    "type": "documents",
                    "props": {
                        "source_strategy": "runtime_contract",
                        "confidence": "definite",
                    },
                })

        # Verification gates → runtime-contract doc. The gate nodes are
        # declared in static topology; if they ever disappear the edges
        # drop silently.
        for gate_id in _RUNTIME_VERIFICATION_GATES:
            edges.append({
                "from": gate_id,
                "to": doc_id,
                "type": "verifies",
                "props": {
                    "source_strategy": "runtime_contract",
                    "confidence": "inferred",
                },
            })

        # Any deploy surfaces present in the graph at merge time link
        # back to the runtime-contract doc. We cannot enumerate them
        # here (we do not see other strategies' outputs), but we can
        # publish the intent as an edge list keyed by a context slot
        # the orchestrator already honors for dangling-endpoint pruning.
        for deploy_id in context.get("deploy_node_ids", ()):
            edges.append({
                "from": deploy_id,
                "to": doc_id,
                "type": "relates_to",
                "props": {
                    "source_strategy": "runtime_contract",
                    "confidence": "inferred",
                },
            })

    return StrategyResult(nodes, edges, discovered_from)
