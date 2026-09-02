"""C# and MSBuild file bodies for the field-eval synthetic workspace.

Split out of :mod:`weld.tests._field_eval_corpus_sources` when the v0.25.0
round added a second .NET child and the payload module could no longer hold
both under the 400-line cap. The seam is the language rather than the round:
every MSBuild/C# body the corpus materialises lives here, which is where a
C#-only producer belongs, and the Python, proto and markdown bodies stayed
next door.

Two children:

* **services/order-gateway** -- the consumer. Its ``.csproj`` declares what it
  consumes and nothing about what it produces; the v0.24.0 corpus is the two
  ``<PackageReference>`` entries, and round three adds a third.
* **libs/billing-schema** -- the producer finding M4 is about. Its published
  package name exists *only* in the ``<PackageId>`` of an MSBuild project: no
  ``pyproject``, no ``go.mod``, no ``.proto``. That makes it the discriminator
  for whether ``package_graph`` derives producers from MSBuild at all, and the
  gateway references it *identically* to the way it references the proto
  library -- two producer-declaration styles consumed the same way.

Treat the evaluator's ``fixture/make-fixture.sh`` as the source of truth for
every body here, exactly as :mod:`weld.tests._field_eval_corpus_sources` says
of its own: if a probe stops reproducing, the first question is whether a body
here drifted from the one the evaluator ran.
"""

from __future__ import annotations


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

#: Where the gateway's project file lands. Written by the materialiser rather
#: than carried in :data:`GATEWAY_FILES`, because its body is *rendered* --
#: the billing reference has to come from the same constant the producer
#: declares, not from a second literal -- see :func:`gateway_csproj`.
GATEWAY_CSPROJ_PATH = "src/OrderGateway.csproj"

#: The name the gateway references for the proto library. Deliberately *not*
#: how that library spells what it publishes -- its ``.proto`` says
#: ``package acme.platform.order.schema.v1`` -- because ``package_graph`` has
#: to join the two case-insensitively; that mismatch is the v0.24.0 corpus's
#: and is preserved.
ORDER_SCHEMA_PACKAGE_REFERENCE = "Acme.Platform.Order.Schema"

#: What ``libs/billing-schema`` publishes, in the one place it says so. The
#: gateway's third reference and the billing ``<PackageId>`` are both rendered
#: from this, so "the consumer names exactly what the producer declares" is a
#: property of the fixture rather than a coincidence between two literals --
#: which is the only thing separating an M4 failure from a typo.
BILLING_PACKAGE_ID = "Acme.Platform.Billing.Schema"

#: All three ``<PackageReference>`` entries matter: the first is the real join
#: to the schema child, the second is what a vendored ``google.protobuf``
#: .proto inside notify-service's .venv fabricates a join to (finding N2), and
#: the third is M4's discriminator -- a reference indistinguishable from the
#: first, to a library that declares itself only in MSBuild.
_GATEWAY_CSPROJ = """\
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <OutputType>Exe</OutputType>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="{order_schema}" Version="1.0.0" />
    <PackageReference Include="Google.Protobuf" Version="3.31.1" />
{billing}  </ItemGroup>
</Project>
"""

_PACKAGE_REFERENCE = '    <PackageReference Include="{name}" Version="1.0.0" />\n'


def gateway_csproj() -> str:
    """The gateway project file, rendered rather than stored as a blob.

    The whole point of M4's fixture is that the schema reference and the
    billing reference are *identical in form*; rendering the second from
    :data:`BILLING_PACKAGE_ID` through the same template is what keeps that
    true of the file rather than true of two literals someone typed.
    """
    return _GATEWAY_CSPROJ.format(
        order_schema=ORDER_SCHEMA_PACKAGE_REFERENCE,
        billing=_PACKAGE_REFERENCE.format(name=BILLING_PACKAGE_ID),
    )


#: Seven C# files -- the count ``wd doctor`` reports in the unclaimed-source
#: warning the N6/N7 probes read. The ``.csproj`` is not among them; the
#: materialiser writes it from :func:`gateway_csproj`.
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
    "doc/order-gateway.md": (
        "# Order Gateway\n\nAccepts orders and replays recorded order logs.\n"
    ),
}

# ------------------------------------------------------- billing schema child

_BILLING_CSPROJ = """\
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <PackageId>{package_id}</PackageId>
    <Version>1.0.0</Version>
  </PropertyGroup>
</Project>
""".format(package_id=BILLING_PACKAGE_ID)

_INVOICE_ISSUED_CS = """\
namespace Acme.Platform.Billing.Schema;

public class InvoiceIssuedEvent
{
    public string InvoiceId { get; set; } = string.Empty;
    public decimal Amount { get; set; }
}
"""

#: Finding M4: two files, and between them not one manifest a package scan has
#: ever read a *produced* name from. The ``<PackageId>`` is the only
#: declaration of what this repo publishes, and it matches the gateway's third
#: ``<PackageReference>`` exactly -- so an edge that fails to form here fails
#: on the producer half, not on name matching.
BILLING_FILES: dict[str, str] = {
    f"src/{BILLING_PACKAGE_ID}.csproj": _BILLING_CSPROJ,
    "src/InvoiceIssuedEvent.cs": _INVOICE_ISSUED_CS,
}
