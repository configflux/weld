"""Shared fixtures for weld benchmark quality tests (project-xoq.7.1).

Provides the synthetic interaction-surface repo used by the quality
benchmark test suite. Extracted to keep individual test files under
the 400-line limit.
"""

from __future__ import annotations

import json
import os
import tempfile

from weld.contract import SCHEMA_VERSION

_TS = "2026-04-10T12:00:00+00:00"

def setup_interaction_repo() -> str:
    """Create a temp repo with interaction-surface nodes for benchmarking."""
    tmpdir = tempfile.mkdtemp()
    weld_dir = os.path.join(tmpdir, ".weld")
    os.makedirs(weld_dir)

    nodes = {
        "service:api": {
            "type": "service",
            "label": "api_service",
            "props": {
                "file": "services/api/main.py",
                "description": "Main API service.",
            },
        },
        "rpc:grpc_store": {
            "type": "rpc",
            "label": "grpc_store_service",
            "props": {
                "file": "services/api/grpc_store.py",
                "description": "gRPC store service endpoint.",
                "protocol": "grpc",
                "surface_kind": "request_response",
            },
        },
        "route:http_health": {
            "type": "route",
            "label": "http_health_route",
            "props": {
                "file": "services/api/health.py",
                "description": "HTTP health check route.",
                "protocol": "http",
                "surface_kind": "request_response",
                "boundary_kind": "inbound",
            },
        },
        "channel:event_orders": {
            "type": "channel",
            "label": "event_order_channel",
            "props": {
                "file": "services/worker/events.py",
                "description": "Event channel for order updates.",
                "protocol": "event",
                "surface_kind": "pub_sub",
            },
        },
        "entity:Store": {
            "type": "entity",
            "label": "Store",
            "props": {
                "file": "src/store.py",
                "description": "Store entity.",
            },
        },
        "boundary:auth_gate": {
            "type": "boundary",
            "label": "auth_boundary",
            "props": {
                "file": "services/api/auth.py",
                "description": "Auth boundary gate.",
                "boundary_kind": "inbound",
            },
        },
        "contract:store_contract": {
            "type": "contract",
            "label": "store_contract",
            "props": {
                "file": "contracts/store.py",
                "description": "Store data contract.",
            },
        },
        "doc:policy_security": {
            "type": "policy",
            "label": "security_policy",
            "props": {
                "file": "docs/security.md",
                "description": "Security policy.",
                "authority": "canonical",
            },
        },
        "test-target:store_test": {
            "type": "test-target",
            "label": "store_test",
            "props": {
                "file": "tests/test_store.py",
                "description": "Store unit tests.",
            },
        },
        "module:discover": {
            "type": "module",
            "label": "discover_module",
            "props": {
                "file": "weld/discover.py",
                "description": "Weld discovery engine.",
            },
        },
        "doc:brief_doc": {
            "type": "doc",
            "label": "brief_contract_doc",
            "props": {
                "file": "weld/docs/brief.md",
                "description": "Brief output contract docs.",
                "authority": "canonical",
            },
        },
        "gate:local_gate": {
            "type": "gate",
            "label": "local_task_gate",
            "props": {
                "file": "local-task-gate",
                "description": "Local task gate script.",
            },
        },
    }

    edges = [
        {
            "from": "service:api",
            "to": "rpc:grpc_store",
            "type": "exposes",
            "props": {},
        },
        {
            "from": "service:api",
            "to": "route:http_health",
            "type": "exposes",
            "props": {},
        },
        {
            "from": "service:api",
            "to": "boundary:auth_gate",
            "type": "contains",
            "props": {},
        },
        {
            "from": "service:api",
            "to": "channel:event_orders",
            "type": "produces",
            "props": {},
        },
        {
            "from": "entity:Store",
            "to": "contract:store_contract",
            "type": "implements",
            "props": {},
        },
        {
            "from": "test-target:store_test",
            "to": "entity:Store",
            "type": "verifies",
            "props": {},
        },
        {
            "from": "doc:brief_doc",
            "to": "module:discover",
            "type": "documents",
            "props": {},
        },
    ]

    graph = {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "git_sha": "bench123",
        },
        "nodes": nodes,
        "edges": edges,
    }
    with open(os.path.join(weld_dir, "graph.json"), "w") as f:
        json.dump(graph, f)

    # Minimal file index.
    with open(os.path.join(weld_dir, "file-index.json"), "w") as f:
        json.dump(
            {
                "meta": {"version": 1, "updated_at": _TS},
                "files": {},
            },
            f,
        )

    return tmpdir

FIXTURE_TASKS_YAML = """\
tasks:
  - id: t01
    prompt: "Where is Store defined?"
    category: navigation
    term: Store
    answer_files:
      - src/store.py
  - id: t02
    prompt: "What depends on Store?"
    category: dependency
    term: Store
    answer_files:
      - src/store.py
      - src/use_store.py
  - id: t03
    prompt: "Who calls Store?"
    category: callgraph
    term: Store
    symbol: Store
    answer_files:
      - src/use_store.py
"""


def setup_compare_repo(term: str = "Store") -> str:
    """Build a synthetic repo for the comparative agent-task bench.

    Small graph + file index + three source files so both grep and weld
    retrieval have something to surface. Kept distinct from
    :func:`setup_interaction_repo` because the two benches exercise
    different graph shapes.
    """
    tmpdir = tempfile.mkdtemp()
    weld_dir = os.path.join(tmpdir, ".weld")
    os.makedirs(weld_dir)
    nodes = {
        "entity:Store": {
            "type": "entity",
            "label": "Store",
            "props": {
                "file": "src/store.py",
                "description": "Store entity.",
            },
        },
        "module:src/use_store.py": {
            "type": "module",
            "label": "use_store",
            "props": {
                "file": "src/use_store.py",
                "description": "Uses Store.",
            },
        },
    }
    edges = [
        {
            "from": "module:src/use_store.py",
            "to": "entity:Store",
            "type": "depends_on",
            "props": {},
        }
    ]
    graph = {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "git_sha": "abc123",
        },
        "nodes": nodes,
        "edges": edges,
    }
    with open(os.path.join(weld_dir, "graph.json"), "w") as f:
        json.dump(graph, f)
    with open(os.path.join(weld_dir, "file-index.json"), "w") as f:
        json.dump(
            {
                "meta": {"version": 1, "updated_at": _TS},
                "files": {
                    "src/store.py": ["src", "store", "Store"],
                    "src/use_store.py": ["src", "use_store", "Store"],
                },
            },
            f,
        )
    src = os.path.join(tmpdir, "src")
    os.makedirs(src)
    for name in ("store.py", "use_store.py", "unrelated.py"):
        with open(os.path.join(src, name), "w") as f:
            if "store" in name:
                f.write(
                    f"# {term} lives here\n"
                    f"class {term}:\n"
                    "    pass\n"
                    + ("# filler\n" * 50)
                )
            else:
                f.write("# nothing relevant\n" + ("# filler\n" * 30))
    return tmpdir


FIXTURE_CASES_YAML = """\
cases:
  - id: c01
    query: "grpc"
    surface: brief
    expect_buckets: [interfaces]
    expect_labels: [grpc]
    category: interaction
  - id: c02
    query: "Store"
    surface: brief
    expect_buckets: [primary]
    expect_labels: [Store]
    category: navigation
  - id: c03
    query: "Store"
    surface: trace
    expect_buckets: [contracts]
    expect_labels: [store_contract]
    category: trace_protocol
  - id: c04
    query: "policy"
    surface: brief
    expect_buckets: [docs]
    expect_labels: [security_policy]
    category: docs
"""
