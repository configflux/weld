"""C# external base-reference resolution.

Extracted from :mod:`weld.strategies._csharp_inheritance` to keep that
module under the 400-line cap.

Background -- the fan-out problem
---------------------------------

The original target resolution minted external base placeholder ids of
the form ``symbol:csharp:<consumingNamespace>.<BareName>`` for bare
base entries that did not resolve to a project file. That worked when
the base lived in the same namespace as the consumer, but caused
fan-out for external types: every consuming namespace that inherited
``Form`` (from ``System.Windows.Forms``) minted its own
``symbol:csharp:<consumer>.Form`` node, breaking cross-namespace
reachability ("who inherits from System.Windows.Forms.Form?").

The fix
-------

Resolution order for a bare base name without a project-file match:

1. **Single external/stdlib using** -- when the consuming file declares
   exactly one ``using`` directive whose namespace is classified
   ``external`` or ``stdlib`` by
   :func:`weld.strategies._csharp_origin.classify_using_import`, use
   that namespace as the FQN prefix
   (``symbol:csharp:<using-ns>.<Bare>``). One canonical node per
   external type, regardless of which consuming namespace inherits
   from it.

2. **Multiple external/stdlib usings** -- ambiguity. Fall back to a
   deterministic single-node bucket
   ``symbol:csharp:_external:<Bare>``. One node per bare name across
   the entire graph; still better than per-consumer fan-out.

3. **No external/stdlib usings visible** -- preserve the legacy
   consuming-namespace shape ``symbol:csharp:<consumer>.<Bare>``. This
   is the dominant in-project pattern (base lives in the same
   namespace, no using needed) and keeps the existing partial-class
   fixtures stable.

Dotted bases (``System.IDisposable``) are not subject to fan-out --
they already carry their declared FQN -- so the resolver short-circuits
on them.

This module owns the placeholder node-id derivation and node minting
only; edge construction stays in
:func:`weld.strategies._csharp_inheritance.emit_base_edges`.
"""

from __future__ import annotations

from weld.strategies._csharp_origin import classify_using_import


def short_name(base_entry: str) -> str:
    """Return the final identifier of a qualified base entry."""
    return base_entry.rsplit(".", 1)[-1]


def resolve_external_base_target(
    nodes: dict[str, dict],
    *,
    namespace: str,
    base_name: str,
    imports: list[str] | None,
    package_references: frozenset[str] | None,
    project_namespace_roots: frozenset[str] | None,
) -> str:
    """Return the canonical id for an unresolved external base.

    See the module docstring for the resolution order. The function
    mints the placeholder symbol node into *nodes* (idempotent via
    ``setdefault``) and returns its id. Caller is responsible for
    constructing the edge body.
    """
    short = short_name(base_name)
    target_namespace = _pick_target_namespace(
        namespace=namespace,
        base_name=base_name,
        short=short,
        imports=imports or [],
        package_references=package_references or frozenset(),
        project_namespace_roots=project_namespace_roots or frozenset(),
    )
    target_id = _build_target_id(target_namespace, short)
    nodes.setdefault(
        target_id,
        _build_placeholder_node(
            short=short,
            target_namespace=target_namespace,
        ),
    )
    return target_id


def _pick_target_namespace(
    *,
    namespace: str,
    base_name: str,
    short: str,
    imports: list[str],
    package_references: frozenset[str],
    project_namespace_roots: frozenset[str],
) -> str:
    """Decide the FQN prefix for the placeholder.

    Dotted bases keep their declared prefix. Bare bases follow the
    single-external-using / ambiguous-external / no-external ladder.
    The empty target-namespace sentinel ``""`` plus the
    ``_external:<Bare>`` short name is what
    :func:`_build_target_id` turns into the ambiguous-bucket id.
    """
    if "." in base_name:
        # Dotted base: declared prefix is already canonical.
        return base_name.rsplit(".", 1)[0]
    external_usings = _external_usings(
        imports=imports,
        package_references=package_references,
        project_namespace_roots=project_namespace_roots,
    )
    if len(external_usings) == 1:
        return external_usings[0]
    if len(external_usings) > 1:
        # Ambiguous bucket sentinel; combined with the short name into
        # ``symbol:csharp:_external:<Bare>`` so the bucket id is one
        # node per bare name across the whole graph.
        return "_external_bucket"
    # No external/stdlib usings -- legacy consuming-namespace fallback.
    return namespace


def _external_usings(
    *,
    imports: list[str],
    package_references: frozenset[str],
    project_namespace_roots: frozenset[str],
) -> list[str]:
    """Return the file-level usings classified as external or stdlib.

    Order-preserving and de-duplicated. ``project``/``unresolved``
    usings are excluded because they cannot own an external base by
    definition.
    """
    result: list[str] = []
    seen: set[str] = set()
    for name in imports:
        if name in seen:
            continue
        seen.add(name)
        origin = classify_using_import(
            name,
            package_references=package_references,
            project_namespace_roots=project_namespace_roots,
        )
        if origin in {"external", "stdlib"}:
            result.append(name)
    return result


def _build_target_id(target_namespace: str, short: str) -> str:
    """Return the placeholder id for a target namespace / bare short.

    ``_external_bucket`` collapses to ``symbol:csharp:_external:<Bare>``
    so the ambiguous bucket lives under a stable, search-friendly
    deterministic id. Empty namespaces collapse to bare
    ``symbol:csharp:<Bare>`` (matches the legacy shape so call-sites
    that pass an empty namespace remain compatible).
    """
    if target_namespace == "_external_bucket":
        return f"symbol:csharp:_external:{short}"
    qualified = f"{target_namespace}.{short}" if target_namespace else short
    return f"symbol:csharp:{qualified}"


def _build_placeholder_node(
    *,
    short: str,
    target_namespace: str,
) -> dict:
    """Build the placeholder symbol-node body.

    The shape mirrors the historical placeholder body from
    :mod:`weld.strategies._csharp_inheritance` so downstream consumers
    (graph linter, search) see no schema change beyond the id itself.
    The ambiguous-bucket marker is recorded in
    ``props.resolution`` so an operator can tell at a glance why the
    placeholder landed in the ``_external:`` namespace.
    """
    is_bucket = target_namespace == "_external_bucket"
    props: dict = {
        "name": short,
        "namespace": "" if is_bucket else target_namespace,
        "kind": "base_reference",
        "language": "csharp",
        "authority": "derived",
        "confidence": "inferred",
        "origin": "external",
        "roles": ["implementation"],
    }
    if is_bucket:
        # Diagnostic crumb for ambiguous-bucket placeholders so a
        # follow-up enrichment pass can distinguish "we know it's
        # external" from "we know which external namespace".
        props["resolution"] = "ambiguous_external"
    return {
        "type": "symbol",
        "label": short,
        "props": props,
    }


__all__ = [
    "resolve_external_base_target",
    "short_name",
]
