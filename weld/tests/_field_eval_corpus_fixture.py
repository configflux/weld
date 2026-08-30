"""Materialises the field-eval v0.23.1 synthetic 4-repo workspace on disk.

The external evaluation (bd ...uuxaz epic) shipped a self-contained synthetic
polyrepo -- ``docs/field-reports/weld-0.23.1-findings/fixture/make-fixture.sh``
-- that reproduced all nine findings with **no proprietary content**: an Acme
"order platform" of a protobuf schema library, a C# gateway that consumes the
schema via a ``<PackageReference>``, a Python notifier that consumes it via a
``pyproject`` dependency, and a docs-only repo, federated under a workspace
root.

The verbatim shell fixture lives under ``docs/field-reports/`` (publish-excluded
and never edited -- it is the report artefact). This module re-expresses the
*load-bearing, grammar-independent* slice of that same synthetic layout as a
Python materialiser the corpus test can drive deterministically in a tempdir:
the manifests the ``package_graph`` resolver reads (``.csproj`` /
``pyproject.toml`` / ``event.proto``), the per-child ``.weld/discover.yaml``
configs, and the ``.weld/workspaces.yaml`` federation config.

It deliberately does **not** reproduce anything that needs an ambient
tree-sitter grammar (the C# source parse): the federated behaviours the corpus
pins -- cross-repo manifest joins, the impact cannot-answer verdict, the doctor
unclaimed-source walk, and the no-graph precondition -- are all computable from
manifests, configs, and hand-shaped graphs alone, exactly as the landed
per-finding regression tests already are.
"""

from __future__ import annotations

from pathlib import Path

# Child names / paths mirror the shell fixture's ``.weld/workspaces.yaml``.
SCHEMA = ("libs-order-schema", "libs/order-schema")
GATEWAY = ("services-order-gateway", "services/order-gateway")
NOTIFY = ("services-notify-service", "services/notify-service")
DOCS = ("docs-site", "docs-site")

CHILDREN: tuple[tuple[str, str], ...] = (SCHEMA, GATEWAY, NOTIFY, DOCS)

# The package name the schema library produces and the two services consume.
# Case differs between the C# ``<PackageReference Include=...>`` and the proto
# ``package`` line on purpose -- the resolver must match case-insensitively.
_SCHEMA_PROTO = """\
syntax = "proto3";

package acme.platform.order.schema.v1;

message OrderPlacedEvent {
  string order_id = 1;
}
"""

_SCHEMA_PYPROJECT = """\
[project]
name = "order-schema"
version = "1.0.0"
"""

_GATEWAY_CSPROJ = """\
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <OutputType>Exe</OutputType>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Acme.Platform.Order.Schema" Version="1.0.0" />
  </ItemGroup>
</Project>
"""

_GATEWAY_CS = """\
using Acme.Platform.Order.Schema.V1;

namespace Acme.Platform.OrderGateway.OrderReplayer;

public class OrderReplayer
{
    public void ReplayOrder(OrderPlacedEvent evt) { }
}
"""

_NOTIFY_PYPROJECT = """\
[project]
name = "notify-service"
version = "1.0.0"
dependencies = ["order-schema>=1.0.0"]
"""

_NOTIFY_MAIN = """\
from acme.platform.order.schema.v1.event_pb2 import OrderPlacedEvent


def handle(evt: OrderPlacedEvent) -> None:
    pass
"""

# Per-child ``discover.yaml`` configs. The gateway's is deliberately
# **markdown-only** -- the Finding-05 shape: a config generated before the C#
# strategy shipped, so 100% of the .cs source is unclaimed while doctor reports
# healthy.
_MARKDOWN_ONLY_CONFIG = """\
sources:
  - glob: "doc/*.md"
    type: doc
    strategy: markdown
    id_prefix: doc:doc
"""

_PY_CONFIG = """\
sources:
  - glob: "**/*.py"
    type: symbol
    strategy: python_module
    id_prefix: symbol:py
"""

_WORKSPACES_YAML = """\
version: 1
scan:
  max_depth: 4
  respect_gitignore: false
children:
  - name: libs-order-schema
    path: libs/order-schema
  - name: services-order-gateway
    path: services/order-gateway
  - name: services-notify-service
    path: services/notify-service
  - name: docs-site
    path: docs-site
cross_repo_strategies: []
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def materialize_workspace(root: Path) -> Path:
    """Lay down the synthetic 4-repo workspace under *root*; return *root*.

    Writes the manifests + configs the corpus assertions read. No ``git init``
    and no tree-sitter parse are required -- everything the corpus pins is
    computable from the files this drops on disk.
    """
    root = Path(root)

    # child: schema (producer)
    _write(
        root / SCHEMA[1] / "src/main/proto/acme/platform/order/schema/v1/event.proto",
        _SCHEMA_PROTO,
    )
    _write(root / SCHEMA[1] / "pyproject.toml", _SCHEMA_PYPROJECT)
    _write(root / SCHEMA[1] / ".weld" / "discover.yaml", _PY_CONFIG)

    # child: C# gateway (consumer via PackageReference) with a markdown-only
    # config -- the Finding-05 stale-config shape.
    _write(root / GATEWAY[1] / "src/OrderReplayer/OrderReplayer.cs", _GATEWAY_CS)
    _write(root / GATEWAY[1] / "OrderGateway.csproj", _GATEWAY_CSPROJ)
    _write(root / GATEWAY[1] / "doc/order-gateway.md", "# Order Gateway\n")
    _write(root / GATEWAY[1] / ".weld" / "discover.yaml", _MARKDOWN_ONLY_CONFIG)

    # child: Python notifier (consumer via pyproject dependency)
    _write(root / NOTIFY[1] / "src/main.py", _NOTIFY_MAIN)
    _write(root / NOTIFY[1] / "pyproject.toml", _NOTIFY_PYPROJECT)
    _write(root / NOTIFY[1] / ".weld" / "discover.yaml", _PY_CONFIG)

    # child: docs-only repo
    _write(root / DOCS[1] / "README.md", "# Platform Documentation\n")
    _write(root / DOCS[1] / "adrs/0001-event-contracts.md", "# ADR 0001\n")

    # workspace root federation config (cross_repo_strategies: [] by default)
    _write(root / ".weld" / "workspaces.yaml", _WORKSPACES_YAML)
    return root
