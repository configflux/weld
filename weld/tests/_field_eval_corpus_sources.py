"""File bodies for the field-eval v0.24.0 synthetic workspace.

Split out of :mod:`weld.tests._field_eval_corpus_fixture` so both stay under
the 400-line cap: this module is *only* the payload (what each fixture file
contains), the fixture module is the layout and the git plumbing.

Every constant here is a faithful transcription of the evaluator's
``fixture/make-fixture.sh`` from the 0.24.0 findings bundle. The bundle is not
committed (user decision, bd ...d76r1) -- this module is where its content
survives, so **treat the shell script as the source of truth**: if a probe in
``weld_field_eval_e2e_test`` stops reproducing, the first question is whether
a body here drifted from the one the evaluator ran.

Load-bearing details that look like noise but are not:

* The proto ``package acme.platform.order.schema.v1`` and the csproj
  ``<PackageReference Include="Acme.Platform.Order.Schema">`` differ in case on
  purpose -- ``package_graph`` must join them case-insensitively.
* ``.venv/.../pandas-3.0.2.dist-info/pyproject.toml`` and
  ``grpc_tools/_proto/google/protobuf/any.proto`` are the *vendored* manifests
  finding N2 is about: a resolver that walks them credits notify-service with
  producing ``pandas`` and ``google.protobuf``.
* ``acme_notify/runner.py`` imports its own sibling by the package's dotted
  name, and ``main.py`` imports ``broker`` bare -- the two shapes finding N4 is
  about.
* ``docs-site`` has five markdown files, one of which is ``README.md`` -- the
  file finding N8 says the fallback source skips.
"""

from __future__ import annotations

# --------------------------------------------------------------- schema child

SCHEMA_PROTO = """\
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
"""

SCHEMA_FILES: dict[str, str] = {
    "src/main/proto/acme/platform/order/schema/v1/event.proto": SCHEMA_PROTO,
    "doc/order-schema.md": (
        "# Order Schema\n\n"
        "Contract definitions shared by every service that handles orders.\n"
    ),
    "pyproject.toml": '[project]\nname = "order-schema"\nversion = "1.0.0"\n',
}

# -------------------------------------------------------------- gateway child

_ORDER_REPLAYER_CS = """\
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
"""

_ORDER_LOG_ENTRY_CS = """\
namespace Acme.Platform.OrderGateway.OrderReplayer;

public class OrderLogEntry
{
    public string OrderId { get; set; } = string.Empty;
    public long Timestamp { get; set; }
}
"""

_IREPLAY_TARGET_CS = """\
using Acme.Platform.Order.Schema.V1;

namespace Acme.Platform.OrderGateway.OrderReplayer;

public interface IReplayTarget
{
    void Send(OrderPlacedEvent evt);
}
"""

_HANDLER_CS = """\
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
"""

#: Both ``<PackageReference>`` entries matter: the first is the real join to
#: the schema child, the second is what a vendored ``google.protobuf`` .proto
#: inside notify-service's .venv fabricates a join to (finding N2).
GATEWAY_CSPROJ = """\
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <OutputType>Exe</OutputType>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Acme.Platform.Order.Schema" Version="1.0.0" />
    <PackageReference Include="Google.Protobuf" Version="3.31.1" />
  </ItemGroup>
</Project>
"""

#: Seven C# files -- the count ``wd doctor`` reports in the unclaimed-source
#: warning the N6/N7 probes read.
GATEWAY_FILES: dict[str, str] = {
    "src/OrderReplayer/OrderReplayer.cs": _ORDER_REPLAYER_CS,
    "src/OrderReplayer/OrderLogEntry.cs": _ORDER_LOG_ENTRY_CS,
    "src/OrderReplayer/IReplayTarget.cs": _IREPLAY_TARGET_CS,
    "src/OrderReplayer/ReplayOptions.cs": (
        "namespace Acme.Platform.OrderGateway.OrderReplayer;\n\n"
        "public class ReplayOptions\n{\n"
        '    public string TargetName { get; set; } = "default";\n'
        "    public int DelayMs { get; set; }\n}\n"
    ),
    "src/OrderReplayer/ReplayProgram.cs": (
        "namespace Acme.Platform.OrderGateway.OrderReplayer;\n\n"
        "public static class ReplayProgram\n{\n"
        "    public static void Main(string[] args) { }\n}\n"
    ),
    "src/OrderReplayer/ReplayUtilities.cs": (
        "namespace Acme.Platform.OrderGateway.OrderReplayer;\n\n"
        "public static class ReplayUtilities\n{\n"
        "    public static string Normalize(string value) => value.Trim();\n}\n"
    ),
    "src/Handlers/OrderPlacedEventHandler.cs": _HANDLER_CS,
    "src/OrderGateway.csproj": GATEWAY_CSPROJ,
    "doc/order-gateway.md": (
        "# Order Gateway\n\nAccepts orders and replays recorded order logs.\n"
    ),
}

#: The file the N5 probe appends to in order to make one child stale.
GATEWAY_TOUCH_FILE = "src/OrderReplayer/ReplayUtilities.cs"

# --------------------------------------------------------------- notify child

_NOTIFY_MAIN = '''\
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
'''

_NOTIFY_BROKER = '''\
"""Minimal broker stand-in."""


class Subscriber:
    async def subscribe(self, event_type, handler) -> None:
        self._registry = getattr(self, "_registry", {})
        self._registry[event_type] = handler
'''

_NOTIFY_HANDLER = '''\
"""Handles OrderPlacedEvent."""

from acme.platform.order.schema.v1.event_pb2 import OrderPlacedEvent


class OrderPlacedHandler:
    async def accept_async(self, event: OrderPlacedEvent) -> None:
        self.last_order_id = event.order_id
'''

_NOTIFY_MULTI_SCHEMA = '''\
"""Imports three distinct schema packages that share a three-segment prefix."""

from acme.platform.order.schema.v1.event_pb2 import OrderPlacedEvent
from acme.platform.order.schema.v2.event_pb2 import OrderPlacedEventV2
from acme.platform.billing.schema.v1.event_pb2 import InvoiceIssuedEvent


def register(subscriber) -> None:
    subscriber.subscribe(OrderPlacedEvent)
    subscriber.subscribe(OrderPlacedEventV2)
    subscriber.subscribe(InvoiceIssuedEvent)
'''

#: Committed in the evaluator's script before the N2/N4 shapes are written.
NOTIFY_FILES: dict[str, str] = {
    "src/main.py": _NOTIFY_MAIN,
    "src/broker.py": _NOTIFY_BROKER,
    "src/handlers/order_placed_handler.py": _NOTIFY_HANDLER,
    "src/handlers/__init__.py": "",
    "src/multi_schema.py": _NOTIFY_MULTI_SCHEMA,
    "tests/test_main.py": "def test_placeholder() -> None:\n    assert True\n",
    "pyproject.toml": (
        "[project]\n"
        'name = "notify-service"\n'
        'version = "1.0.0"\n'
        'dependencies = ["order-schema>=1.0.0"]\n'
    ),
}

_ACME_NOTIFY_CONFIG = '''\
"""First-party configuration module, in this same repository."""

DEFAULT_RETRIES = 3


def load_config(path: str) -> dict:
    return {"path": path, "retries": DEFAULT_RETRIES}
'''

_ACME_NOTIFY_RUNNER = '''\
"""Imports a sibling first-party module by its own package name."""

from acme_notify.config import load_config, DEFAULT_RETRIES


def run(path: str) -> dict:
    cfg = load_config(path)
    cfg["retries"] = DEFAULT_RETRIES
    return cfg
'''

#: Finding N4: a first-party package imported by its own dotted name. Written
#: *after* the notify commit in make-fixture.sh, so these arrive untracked --
#: reproduced faithfully, because "untracked but not ignored" is precisely the
#: git-visibility state the N2 fix has to keep discovering.
NOTIFY_FIRST_PARTY_FILES: dict[str, str] = {
    "src/acme_notify/__init__.py": "",
    "src/acme_notify/config.py": _ACME_NOTIFY_CONFIG,
    "src/acme_notify/runner.py": _ACME_NOTIFY_RUNNER,
}

_VENV = ".venv/lib/python3.12/site-packages"

#: Finding N2: a real .venv is ~1 GB; only the metadata a manifest scan reads
#: is recreated. ``.gitignore`` hides the whole tree from git, which is what
#: makes "the resolver walked it anyway" the defect.
NOTIFY_VENDORED_FILES: dict[str, str] = {
    f"{_VENV}/pandas-3.0.2.dist-info/METADATA": (
        "Metadata-Version: 2.1\n"
        "Name: pandas\n"
        "Version: 3.0.2\n"
        "Summary: Powerful data structures for data analysis\n"
    ),
    f"{_VENV}/pandas-3.0.2.dist-info/RECORD": "pandas/__init__.py,,\n",
    f"{_VENV}/pandas-3.0.2.dist-info/pyproject.toml": (
        '[project]\nname = "pandas"\nversion = "3.0.2"\n'
    ),
    f"{_VENV}/pandas/__init__.py": '__version__ = "3.0.2"\n',
    f"{_VENV}/grpc_tools/_proto/google/protobuf/any.proto": (
        'syntax = "proto3";\n\n'
        "package google.protobuf;\n\n"
        "message Any {\n"
        "  string type_url = 1;\n"
        "  bytes value = 2;\n"
        "}\n"
    ),
    f"{_VENV}/grpc_tools/__init__.py": "",
    ".gitignore": ".venv/\n",
}

# ----------------------------------------------------------------- docs child

#: Five markdown files. ``README.md`` is the one finding N8 reports missing
#: from the graph, and its H1 is the term the N8 query probe searches for.
DOCS_FILES: dict[str, str] = {
    "README.md": (
        "# Platform Documentation\n\n"
        "Index of architecture decisions and platform guides.\n"
    ),
    "platform-overview.md": (
        "# Platform Overview\n\nHow the order pipeline fits together.\n"
    ),
    "adrs/0001-event-contracts.md": (
        "# ADR 0001: Event Contracts\n\n"
        "We version every event contract in a dedicated schema repository.\n"
    ),
    "adrs/0002-service-boundaries.md": (
        "# ADR 0002: Service Boundaries\n\nEach service owns its datastore.\n"
    ),
    "architecture/data-flow.md": (
        "# Data Flow\n\nOrders flow from the gateway to the notifier.\n"
    ),
    "pyproject.toml": (
        "[project]\n"
        'name = "docs-site"\n'
        'version = "1.0.0"\n'
        'dependencies = ["pandas>=2.0"]\n'
    ),
}

# ------------------------------------------------ probe-time config mutation

#: The team's narrowed, hand-maintained ``discover.yaml`` that the N6/N7 probe
#: writes over the gateway's generated one (``run-all-repros.sh``). The comment
#: and the entry nothing auto-detects are load-bearing: they are what
#: ``wd init --refresh`` must preserve and ``--force`` must be seen to discard.
HAND_EDITED_GATEWAY_CONFIG = """\
# Hand-maintained config. Do not clobber.
sources:
  # Custom: deliberately narrowed by the team.
  - glob: "doc/*.md"
    type: doc
    strategy: markdown
    id_prefix: doc:doc

  # Custom entry nothing auto-detects:
  - files: ["OrderGateway.sln.DotSettings"]
    type: config
    strategy: config_file
"""

