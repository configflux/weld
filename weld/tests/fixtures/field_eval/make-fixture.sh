#!/usr/bin/env bash
# Creates a synthetic Weld polyrepo workspace that exercises the findings reported
# against 0.23.1 and 0.24.0, plus the Python import shapes fixed in 0.25.0.
# No proprietary content. Everything lands under $TARGET.
#
#   ./make-fixture.sh [TARGET_DIR]     (default: ./weld-repro-workspace)
#
#   <root>/                       git repo, federation root (.weld/workspaces.yaml tracked)
#     libs/order-schema/          git repo, protobuf contracts
#     services/order-gateway/     git repo, C# (tree-sitter), consumes schema + Google.Protobuf
#     services/notify-service/    git repo, Python: dotted imports, first-party package,
#                                 relative/sibling/lazy-api shapes, vendored .venv
#     docs-site/                  git repo, markdown at root + adrs/, depends on pandas

set -euo pipefail

TARGET="${1:-$PWD/weld-repro-workspace}"
rm -rf "$TARGET"
mkdir -p "$TARGET"
TARGET="$(cd "$TARGET" && pwd)"

git_init() {
    git -C "$1" init -q
    git -C "$1" config user.email "fixture@example.com"
    git -C "$1" config user.name "Fixture"
}
git_commit() {
    git -C "$1" add -A
    git -C "$1" -c commit.gpgsign=false commit -q -m "${2:-fixture}"
}

# ---------------------------------------------------------------- child: schema
S="$TARGET/libs/order-schema"
mkdir -p "$S/src/main/proto/acme/platform/order/schema/v1" "$S/doc"
cat > "$S/src/main/proto/acme/platform/order/schema/v1/event.proto" <<'EOF'
syntax = "proto3";

package acme.platform.order.schema.v1;

// Emitted when an order is accepted by the gateway.
message OrderPlacedEvent {
  string order_id = 1;
  string customer_id = 2;
  int64 placed_at = 3;
}

// Emitted when an order leaves the warehouse.
message OrderShippedEvent {
  string order_id = 1;
  string carrier = 2;
  int64 shipped_at = 3;
}

// Emitted when an order can no longer be fulfilled.
message OrderCancelledEvent {
  string order_id = 1;
  string reason = 2;
}

enum OrderStateEnum {
  ORDER_STATE_UNSPECIFIED = 0;
  ORDER_STATE_PLACED = 1;
  ORDER_STATE_SHIPPED = 2;
  ORDER_STATE_CANCELLED = 3;
}
EOF
cat > "$S/doc/order-schema.md" <<'EOF'
# Order Schema

Contract definitions shared by every service that handles orders.
EOF
cat > "$S/pyproject.toml" <<'EOF'
[project]
name = "order-schema"
version = "1.0.0"
EOF
git_init "$S"; git_commit "$S" "order schema"

# ------------------------------------------- child: C#-only schema library
# Its produced package name exists ONLY in a .csproj -- no .proto, no pyproject --
# so it isolates whether package_graph derives producers from MSBuild projects.
B="$TARGET/libs/billing-schema"
mkdir -p "$B/src"
cat > "$B/src/Acme.Platform.Billing.Schema.csproj" <<'EOF'
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <PackageId>Acme.Platform.Billing.Schema</PackageId>
    <Version>1.0.0</Version>
  </PropertyGroup>
</Project>
EOF
cat > "$B/src/InvoiceIssuedEvent.cs" <<'EOF'
namespace Acme.Platform.Billing.Schema;

public class InvoiceIssuedEvent
{
    public string InvoiceId { get; set; } = string.Empty;
    public decimal Amount { get; set; }
}
EOF
git_init "$B"; git_commit "$B" "billing schema"

# ------------------------------------------------------- child: C# service
G="$TARGET/services/order-gateway"
mkdir -p "$G/src/OrderReplayer" "$G/src/Handlers" "$G/doc"
cat > "$G/src/OrderReplayer/OrderReplayer.cs" <<'EOF'
using System;
using System.Collections.Generic;
using Acme.Platform.Order.Schema.V1;

namespace Acme.Platform.OrderGateway.OrderReplayer;

/// <summary>Replays a recorded order log against a running gateway.</summary>
public class OrderReplayer
{
    private readonly IReplayTarget _target;

    public OrderReplayer(IReplayTarget target) => _target = target;

    public void ReplayOrder(OrderLogEntry entry)
    {
        var evt = new OrderPlacedEvent { OrderId = entry.OrderId };
        _target.Send(evt);
    }

    public void ReplayOrderLog(IEnumerable<OrderLogEntry> entries)
    {
        foreach (var entry in entries) ReplayOrder(entry);
    }

    public string SerializeOrderMessage(OrderPlacedEvent evt) => evt.ToString();
}
EOF
cat > "$G/src/OrderReplayer/OrderLogEntry.cs" <<'EOF'
namespace Acme.Platform.OrderGateway.OrderReplayer;

public class OrderLogEntry
{
    public string OrderId { get; set; } = string.Empty;
    public long Timestamp { get; set; }
}
EOF
cat > "$G/src/OrderReplayer/IReplayTarget.cs" <<'EOF'
using Acme.Platform.Order.Schema.V1;

namespace Acme.Platform.OrderGateway.OrderReplayer;

public interface IReplayTarget
{
    void Send(OrderPlacedEvent evt);
}
EOF
cat > "$G/src/OrderReplayer/ReplayOptions.cs" <<'EOF'
namespace Acme.Platform.OrderGateway.OrderReplayer;

public class ReplayOptions
{
    public string TargetName { get; set; } = "default";
    public int DelayMs { get; set; }
}
EOF
cat > "$G/src/OrderReplayer/ReplayProgram.cs" <<'EOF'
namespace Acme.Platform.OrderGateway.OrderReplayer;

public static class ReplayProgram
{
    public static void Main(string[] args) { }
}
EOF
cat > "$G/src/OrderReplayer/ReplayUtilities.cs" <<'EOF'
namespace Acme.Platform.OrderGateway.OrderReplayer;

public static class ReplayUtilities
{
    public static string Normalize(string value) => value.Trim();
}
EOF
cat > "$G/src/Handlers/OrderPlacedEventHandler.cs" <<'EOF'
using Acme.Platform.Order.Schema.V1;

namespace Acme.Platform.OrderGateway.Handlers;

/// <summary>Produces an OrderShippedEvent once an order is picked.</summary>
public class OrderPlacedEventHandler
{
    public OrderShippedEvent Handle(OrderPlacedEvent placed)
    {
        return new OrderShippedEvent { OrderId = placed.OrderId, Carrier = "default" };
    }
}
EOF
cat > "$G/src/OrderGateway.csproj" <<'EOF'
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <OutputType>Exe</OutputType>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Acme.Platform.Order.Schema" Version="1.0.0" />
    <PackageReference Include="Google.Protobuf" Version="3.31.1" />
    <PackageReference Include="Acme.Platform.Billing.Schema" Version="1.0.0" />
  </ItemGroup>
</Project>
EOF
cat > "$G/doc/order-gateway.md" <<'EOF'
# Order Gateway

Accepts orders and replays recorded order logs.
EOF
git_init "$G"; git_commit "$G" "order gateway"

# --------------------------------------------------- child: Python service
N="$TARGET/services/notify-service"
mkdir -p "$N/src/handlers" "$N/tests"
cat > "$N/src/main.py" <<'EOF'
"""Notify service entrypoint."""

import asyncio

from acme.platform.order.schema.v1.event_pb2 import OrderPlacedEvent
from acme.platform.order.schema.v1.event_pb2 import OrderShippedEvent

from handlers.order_placed_handler import OrderPlacedHandler
from broker import Subscriber


async def main() -> None:
    subscriber = Subscriber()
    # NOTE: the event type is passed as a handle, never called.
    await subscriber.subscribe(OrderPlacedEvent, OrderPlacedHandler())
    await subscriber.subscribe(OrderShippedEvent, OrderPlacedHandler())


if __name__ == "__main__":
    asyncio.run(main())
EOF
cat > "$N/src/broker.py" <<'EOF'
"""Minimal broker stand-in."""


class Subscriber:
    async def subscribe(self, event_type, handler) -> None:
        self._registry = getattr(self, "_registry", {})
        self._registry[event_type] = handler
EOF
cat > "$N/src/handlers/order_placed_handler.py" <<'EOF'
"""Handles OrderPlacedEvent."""

from acme.platform.order.schema.v1.event_pb2 import OrderPlacedEvent


class OrderPlacedHandler:
    async def accept_async(self, event: OrderPlacedEvent) -> None:
        self.last_order_id = event.order_id
EOF
cat > "$N/src/handlers/__init__.py" <<'EOF'
EOF
cat > "$N/src/multi_schema.py" <<'EOF'
"""Imports three distinct schema packages that share a three-segment prefix."""

from acme.platform.order.schema.v1.event_pb2 import OrderPlacedEvent
from acme.platform.order.schema.v2.event_pb2 import OrderPlacedEventV2
from acme.platform.billing.schema.v1.event_pb2 import InvoiceIssuedEvent


def register(subscriber) -> None:
    subscriber.subscribe(OrderPlacedEvent)
    subscriber.subscribe(OrderPlacedEventV2)
    subscriber.subscribe(InvoiceIssuedEvent)
EOF
cat > "$N/tests/test_main.py" <<'EOF'
def test_placeholder() -> None:
    assert True
EOF
cat > "$N/pyproject.toml" <<'EOF'
[project]
name = "notify-service"
version = "1.0.0"
dependencies = ["order-schema>=1.0.0"]
EOF

# --- N4 shape: a first-party package imported by its own dotted name ---
mkdir -p "$N/src/acme_notify"
cat > "$N/src/acme_notify/__init__.py" <<'EOF'
EOF
cat > "$N/src/acme_notify/config.py" <<'EOF'
"""First-party configuration module, in this same repository."""

DEFAULT_RETRIES = 3


def load_config(path: str) -> dict:
    return {"path": path, "retries": DEFAULT_RETRIES}
EOF
cat > "$N/src/acme_notify/runner.py" <<'EOF'
"""Imports a sibling first-party module by its own package name."""

from acme_notify.config import load_config, DEFAULT_RETRIES


def run(path: str) -> dict:
    cfg = load_config(path)
    cfg["retries"] = DEFAULT_RETRIES
    return cfg
EOF

# --- 0.25.0 shape: explicit relative import inside a package ---
cat > "$N/src/acme_notify/helper.py" <<'EOF'
"""Target of an explicit relative import."""


def work(value: int) -> int:
    return value * 2
EOF
cat > "$N/src/acme_notify/relative_caller.py" <<'EOF'
"""Calls a sibling module through an explicit relative import."""

from .helper import work


def double_it(value: int) -> int:
    return work(value)
EOF

# --- 0.25.0 shape: lazy-api helper whose return value is unpacked ---
cat > "$N/src/acme_notify/lazy_api.py" <<'EOF'
"""The cycle-breaking idiom: an import hidden in a function, unpacked at the call site."""


def _api():
    from acme_notify.config import load_config, DEFAULT_RETRIES

    return load_config, DEFAULT_RETRIES


def build(path: str) -> dict:
    load_config, DEFAULT_RETRIES = _api()
    cfg = load_config(path)
    cfg["retries"] = DEFAULT_RETRIES
    return cfg
EOF

# --- 0.25.0 shape: classmethod reached through an imported class ---
cat > "$N/src/acme_notify/corpus.py" <<'EOF'
"""Class exposing a classmethod used by another module."""


class Corpus:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    @classmethod
    def build(cls, rows: list) -> "Corpus":
        return cls(rows)

    @staticmethod
    def empty() -> "Corpus":
        return Corpus([])
EOF
cat > "$N/src/acme_notify/corpus_user.py" <<'EOF'
"""Calls a classmethod through the imported class."""

from acme_notify.corpus import Corpus


def make(rows: list) -> Corpus:
    return Corpus.build(rows)


def make_empty() -> Corpus:
    return Corpus.empty()
EOF

# --- 0.25.0 shape: sibling bare-name import in a script dir (no __init__.py) ---
mkdir -p "$N/scripts"
cat > "$N/scripts/shared_helper.py" <<'EOF'
"""A helper sitting beside its caller in a directory with no __init__.py."""


def shared_work(value: str) -> str:
    return value.strip().lower()
EOF
cat > "$N/scripts/run_report.py" <<'EOF'
"""Imports its neighbour by bare name -- the interpreter puts this dir on sys.path."""

from shared_helper import shared_work


def report(value: str) -> str:
    return shared_work(value)
EOF

# --- N2 shape: a vendored dependency tree inside a child repo ---
# A real .venv is ~1 GB; only the metadata the resolver reads is recreated here.
VENV="$N/.venv/lib/python3.12/site-packages"
mkdir -p "$VENV/pandas-3.0.2.dist-info" "$VENV/grpc_tools/_proto/google/protobuf" "$VENV/pandas"
cat > "$VENV/pandas-3.0.2.dist-info/METADATA" <<'EOF'
Metadata-Version: 2.1
Name: pandas
Version: 3.0.2
Summary: Powerful data structures for data analysis
EOF
cat > "$VENV/pandas-3.0.2.dist-info/RECORD" <<'EOF'
pandas/__init__.py,,
EOF
cat > "$VENV/pandas/__init__.py" <<'EOF'
__version__ = "3.0.2"
EOF
cat > "$VENV/pandas-3.0.2.dist-info/pyproject.toml" <<'EOF'
[project]
name = "pandas"
version = "3.0.2"
EOF
cat > "$VENV/grpc_tools/_proto/google/protobuf/any.proto" <<'EOF'
syntax = "proto3";

package google.protobuf;

message Any {
  string type_url = 1;
  bytes value = 2;
}
EOF
cat > "$VENV/grpc_tools/__init__.py" <<'EOF'
EOF
cat > "$N/.gitignore" <<'EOF'
.venv/
EOF
git_init "$N"; git_commit "$N" "notify service"

# ------------------------------------------------------ child: docs-only repo
D="$TARGET/docs-site"
mkdir -p "$D/adrs" "$D/architecture"
cat > "$D/README.md" <<'EOF'
# Platform Documentation

Index of architecture decisions and platform guides.
EOF
cat > "$D/platform-overview.md" <<'EOF'
# Platform Overview

How the order pipeline fits together.
EOF
cat > "$D/adrs/0001-event-contracts.md" <<'EOF'
# ADR 0001: Event Contracts

We version every event contract in a dedicated schema repository.
EOF
cat > "$D/adrs/0002-service-boundaries.md" <<'EOF'
# ADR 0002: Service Boundaries

Each service owns its datastore.
EOF
cat > "$D/architecture/data-flow.md" <<'EOF'
# Data Flow

Orders flow from the gateway to the notifier.
EOF
cat > "$D/pyproject.toml" <<'EOF'
[project]
name = "docs-site"
version = "1.0.0"
dependencies = ["pandas>=2.0"]
EOF
git_init "$D"; git_commit "$D" "docs"

# ------------------------------------------------------------- workspace root
mkdir -p "$TARGET/.weld"
cat > "$TARGET/.weld/workspaces.yaml" <<'EOF'
version: 1
scan:
  max_depth: 4
  respect_gitignore: false
  exclude_paths: [.worktrees]
children:
  - name: libs-order-schema
    path: libs/order-schema
  - name: libs-billing-schema
    path: libs/billing-schema
  - name: services-order-gateway
    path: services/order-gateway
  - name: services-notify-service
    path: services/notify-service
  - name: docs-site
    path: docs-site
cross_repo_strategies: []
EOF
cat > "$TARGET/.weld/.gitignore" <<'EOF'
# Track the shared workspace config; ignore generated local state.
*
!.gitignore
!workspaces.yaml
EOF
cat > "$TARGET/README.md" <<'EOF'
# Acme Platform (workspace root)

Federated polyrepo workspace: schema library, two services, and a docs repo.
EOF
git_init "$TARGET"
cat > "$TARGET/.gitignore" <<'EOF'
.worktrees/
libs/
services/
docs-site/
EOF
git_commit "$TARGET" "workspace root"

printf 'Fixture ready: %s\n' "$TARGET"
