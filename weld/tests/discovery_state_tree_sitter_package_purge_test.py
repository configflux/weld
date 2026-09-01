"""Unit contract for the tree-sitter using/import package purge (bd 5ouuf;
widened to project/stdlib origins by bd cs0rt).

:func:`weld.strategies._csharp_tree_sitter._add_import_dependencies` and its
Java sibling mint a ``package`` node for every ``using``/``import`` target,
carrying ``props.source_strategy == "tree_sitter"`` and ``props.authority ==
"derived"`` -- never ``"external"``, so this shape sits entirely outside
:mod:`weld.tests.discovery_state_manifest_dependency_purge_test`'s
``_EDGE_ANCHORED_STRATEGIES`` allowlist (that allowlist's predicate also
requires ``authority == "external"``). Investigated first under bd ukt95
(found NOT to share that shape) and re-investigated under bd 5ouuf for the
DIFFERENT scenario ukt95 was never scoped to cover: last importing
``.cs``/``.java`` file deleted, manifest untouched.

Empirically confirmed with real grammars (``tree_sitter_c_sharp``,
``tree_sitter_java`` -- ambient pip installs, not vendored in this repo's
lockfile, so not exercised under ``bazel test`` here; see the bd 5ouuf and
bd cs0rt spec-lock comments for the exact repro scripts and captured graphs)
via the real ``weld.discover._discover_single_repo`` orchestrator,
incremental vs full:

* C# external (a ``.csproj`` with a ``PackageReference`` to
  ``Newtonsoft.Json`` plus a ``Program.cs`` ``using Newtonsoft.Json;``):
  deleting ``Program.cs`` (the sole importer) leaves
  ``package:csharp:newtonsoft.json`` behind in incremental with zero inbound
  ``depends_on`` edges; a fresh full discover of the same post-delete tree
  mints nothing.
* Java unresolved (a ``pom.xml`` dependency on
  ``com.fasterxml.jackson.core:jackson-databind`` plus an ``App.java``
  ``import com.fasterxml.jackson.databind.ObjectMapper;`` -- the import
  sub-package does not match the declared groupId closely enough for
  ``classify_import_package`` to resolve ``"external"``, landing on
  ``"unresolved"`` instead, itself a useful confirmation that this origin is
  a realistic runtime outcome and not just a theoretical fallback): same
  divergence, deleting ``App.java``.
* C# project-origin (a project declaring ``<RootNamespace>MyApp</RootNamespace>``
  with a file that declares only ``namespace MyApp.Deep`` and a SEPARATE
  file that does ``using MyApp.Deep.Nested;`` -- a namespace no file ever
  declares as its own): ``package:csharp:myapp.deep.nested`` (``origin:
  "project"``) leaks IDENTICALLY. bd 5ouuf left this origin unpurged on an
  asymmetry-of-harm argument (a false negative here seemed cheaper than a
  false positive elsewhere in the family); bd cs0rt closed the gap after
  showing that argument's premise -- that a false positive is even possible
  here -- does not hold: re-running this exact fixture with ``Deep.cs``
  changed to declare ``namespace MyApp.Deep.Nested`` (the id the shell
  would otherwise occupy) resolves to the producer shape
  (:func:`weld.strategies.csharp_package.extract`'s ``roles: ["package"]``
  node) in BOTH full and incremental discovery, never the shell -- see
  :mod:`weld._discover_tree_sitter_package_purge`'s module docstring for the
  full merge-order argument this empirical result confirms.
* Java has no producer-side package strategy at all, so a Java
  ``origin: "project"`` shell (an internal ``import`` between two packages
  under the project's own Maven groupId) carries zero collision risk --
  strictly safer than the C# case above.

These tests exercise
:func:`weld._discover_tree_sitter_package_purge.emptied_tree_sitter_package_node_ids`
and :func:`weld._discover_external_package_purge.emptied_placeholder_node_ids`
directly against small synthetic graphs built from the real captured
fingerprints above -- the same level every sibling module in this family
tests at -- rather than through a full ``discover()`` pipeline, since the
C#/Java grammars are not Bazel-hermetic here (see the module docstring of
:mod:`weld.tests.discovery_state_manifest_dependency_purge_test` for the
established precedent: bd ukt95/bd 0cobr also shipped unit-level-only
coverage for this exact reason). The integrated ``purge_stale_nodes``
call-site tests live in a sibling file, split out to keep both under the
400-line cap (the same split ukt95 and bd n4nvt already took for
``discovery_state_external_package_purge_test.py`` /
``discovery_state_manifest_dependency_purge_test.py`` and
``discovery_state_resolved_stub_purge_test.py`` /
``discovery_state_resolved_stub_integration_test.py`` respectively):
``weld/tests/discovery_state_tree_sitter_package_purge_integration_test.py``.
"""

from __future__ import annotations

import unittest

from weld._discover_external_package_purge import emptied_placeholder_node_ids
from weld._discover_tree_sitter_package_purge import (
    emptied_tree_sitter_package_node_ids,
)


def _tree_sitter_package(
    name: str, *, language: str = "csharp", origin: str = "external",
) -> dict:
    """The ``_add_import_dependencies`` shape shared by C# and Java --
    real fingerprint (barring ``name``/``language``/``origin``) captured via
    an actual ``tree_sitter_c_sharp``/``tree_sitter_java`` parse (bd 5ouuf;
    see the module docstring)."""
    return {
        "type": "package", "label": name,
        "props": {
            "name": name, "language": language,
            "source_strategy": "tree_sitter", "authority": "derived",
            "confidence": "definite", "origin": origin,
        },
    }


def _producer_package(name: str = "MyApp", *, source_strategy: str = "csharp_package") -> dict:
    """A producer-side ``roles: ["package"]`` node at the SAME id
    namespace -- e.g. ``csharp_package.extract``'s unconditional
    ``nodes[pkg_nid] = {...}`` overwrite always leaves namespaces it anchors
    in THIS shape, never the tree-sitter shape above, regardless of which
    strategy ran first. Disjoint from this rule on ``source_strategy``
    alone; g7rs's own rule (zero outgoing ``contains``) is what protects it."""
    return {
        "type": "package", "label": name,
        "props": {
            "name": name, "language": "csharp",
            "source_strategy": source_strategy, "authority": "derived",
            "confidence": "definite", "roles": ["package"], "origin": "project",
        },
    }


def _depends_on(frm: str, to: str) -> dict:
    return {
        "from": frm, "to": to, "type": "depends_on",
        "props": {"source_strategy": "tree_sitter", "confidence": "definite"},
    }


class EmptiedTreeSitterPackageNodeIdsTest(unittest.TestCase):
    """The pure predicate, isolated from the purge call site."""

    def test_csharp_external_zero_inbound_is_emptied(self) -> None:
        nodes = {"package:csharp:newtonsoft.json": _tree_sitter_package("Newtonsoft.Json")}
        self.assertEqual(
            emptied_tree_sitter_package_node_ids(nodes, []),
            {"package:csharp:newtonsoft.json"},
        )

    def test_csharp_unresolved_zero_inbound_is_also_emptied(self) -> None:
        nodes = {
            "package:csharp:widget": _tree_sitter_package("Widget", origin="unresolved"),
        }
        self.assertEqual(
            emptied_tree_sitter_package_node_ids(nodes, []), {"package:csharp:widget"},
        )

    def test_java_external_zero_inbound_is_also_emptied(self) -> None:
        nodes = {
            "package:java:com.google.gson": _tree_sitter_package(
                "com.google.gson", language="java",
            ),
        }
        self.assertEqual(
            emptied_tree_sitter_package_node_ids(nodes, []),
            {"package:java:com.google.gson"},
        )

    def test_java_unresolved_zero_inbound_is_also_emptied(self) -> None:
        """The reported repro's actual origin value (bd 5ouuf): a pom.xml
        dependency groupId that does not closely match the imported
        sub-package resolves to "unresolved", not "external" -- both must be
        covered, and this is proof "unresolved" is a real, not merely
        theoretical, runtime outcome."""
        nodes = {
            "package:java:com.fasterxml.jackson.databind": _tree_sitter_package(
                "com.fasterxml.jackson.databind", language="java", origin="unresolved",
            ),
        }
        self.assertEqual(
            emptied_tree_sitter_package_node_ids(nodes, []),
            {"package:java:com.fasterxml.jackson.databind"},
        )

    def test_csharp_project_origin_zero_inbound_is_also_emptied(self) -> None:
        """bd cs0rt: the project-origin leak bd 5ouuf left unpurged on an
        asymmetry-of-harm argument is closed -- the argument's premise (a
        false positive is even possible here) does not hold, since this
        predicate's ``source_strategy`` check already makes any
        producer-anchored id categorically unreachable. See the module
        docstring of :mod:`weld._discover_tree_sitter_package_purge` for
        the full argument and the empirical collision proof."""
        nodes = {
            "package:csharp:myapp.deep.nested": _tree_sitter_package(
                "MyApp.Deep.Nested", origin="project",
            ),
        }
        self.assertEqual(
            emptied_tree_sitter_package_node_ids(nodes, []),
            {"package:csharp:myapp.deep.nested"},
        )

    def test_csharp_stdlib_zero_inbound_is_also_emptied(self) -> None:
        nodes = {"package:csharp:system": _tree_sitter_package("System", origin="stdlib")}
        self.assertEqual(
            emptied_tree_sitter_package_node_ids(nodes, []), {"package:csharp:system"},
        )

    def test_java_project_origin_zero_inbound_is_also_emptied(self) -> None:
        """Java symmetry: no producer-side package strategy exists for Java
        at all (no ``java_package.py``), so a Java project-origin shell
        carries zero collision risk -- strictly safer than the C# case
        above, which still gets purged once source_strategy/authority
        already exclude any producer-anchored id."""
        nodes = {
            "package:java:com.example.foo": _tree_sitter_package(
                "com.example.foo", language="java", origin="project",
            ),
        }
        self.assertEqual(
            emptied_tree_sitter_package_node_ids(nodes, []),
            {"package:java:com.example.foo"},
        )

    def test_unknown_origin_value_is_never_emptied(self) -> None:
        """Defensive boundary now that all four ADR 0042 buckets are
        purgeable: an origin value outside that vocabulary (a future bucket,
        a typo, a project-local strategy override) must not purge by
        default -- ``_PURGEABLE_ORIGINS`` stays an explicit allowlist rather
        than "purge unless external/unresolved is excluded"."""
        nodes = {
            "package:csharp:mystery": _tree_sitter_package("Mystery", origin="vendored"),
        }
        self.assertEqual(emptied_tree_sitter_package_node_ids(nodes, []), set())

    def test_wrong_authority_is_never_emptied(self) -> None:
        """A graph_closure/cpp_conan-shaped node (``authority: "external"``)
        must never match here -- that shape is
        ``discovery_state_external_package_purge_test``'s own rule's job,
        disjoint from this one on the authority value alone."""
        nodes = {
            "package:go:strings": {
                "type": "package", "label": "strings",
                "props": {
                    "source_strategy": "tree_sitter", "authority": "external",
                    "confidence": "inferred", "origin": "external",
                },
            },
        }
        self.assertEqual(emptied_tree_sitter_package_node_ids(nodes, []), set())

    def test_wrong_source_strategy_is_never_emptied(self) -> None:
        """bd cs0rt's core safety claim, pinned at the exact id the real
        fixture leaks at: a csharp_package PRODUCER node (``roles:
        ["package"]``, minted from an actually-declared namespace) at
        ``package:csharp:myapp.deep.nested`` -- the SAME id a tree-sitter
        shell would occupy for ``using MyApp.Deep.Nested;`` -- must never
        match here regardless of ``origin`` or inbound edge count; g7rs's
        rule owns it."""
        nodes = {
            "package:csharp:myapp.deep.nested": _producer_package("MyApp.Deep.Nested"),
        }
        self.assertEqual(emptied_tree_sitter_package_node_ids(nodes, []), set())

    def test_live_inbound_edge_is_not_emptied(self) -> None:
        nodes = {"package:csharp:newtonsoft.json": _tree_sitter_package("Newtonsoft.Json")}
        edges = [_depends_on("file:Program", "package:csharp:newtonsoft.json")]
        self.assertEqual(emptied_tree_sitter_package_node_ids(nodes, edges), set())

    def test_missing_props_is_defensive(self) -> None:
        """Strategy-authored props (including project-local overrides under
        ``.weld/strategies/``) are untrusted shape -- a missing/``None``
        ``props`` must read as "not eligible", never raise."""
        nodes = {"package:csharp:bad": {"type": "package", "label": "bad", "props": None}}
        self.assertEqual(emptied_tree_sitter_package_node_ids(nodes, []), set())

    def test_non_dict_truthy_props_is_defensive(self) -> None:
        """The same defensiveness for a truthy but non-dict ``props`` (the
        ``props or {}`` fallback above only normalises falsy values --
        this exercises the ``isinstance`` guard specifically)."""
        nodes = {
            "package:csharp:bad": {"type": "package", "label": "bad", "props": "garbage"},
        }
        self.assertEqual(emptied_tree_sitter_package_node_ids(nodes, []), set())

    def test_a_prop_value_of_any_type_answers_rather_than_raising(self) -> None:
        """The VALUE half of the defensiveness the two tests above cover only
        the SHAPE of (bd 5038-53jjg).

        ``props`` is read back off ``.weld/graph.json``, which ADR 0115 treats
        as unvetted repo text, so a well-formed dict can still carry
        ``"origin": []`` -- and ``origin`` is the one key this predicate tests
        by membership in :data:`_PURGEABLE_ORIGINS`, where an unhashable value
        raised ``TypeError`` rather than answering, aborting this purge and the
        whole incremental discover around it. Found by the sibling sweep bd
        5038-53jjg ran after fixing the identical shape in
        :mod:`weld._discover_external_package_purge`; ``_is_derived_edge``
        (bd 5038-rwi34) is the guard both now use.

        All three prop keys the predicate reads are exercised, not just the
        membership one: ``source_strategy`` and ``authority`` are compared with
        ``==`` and were already total over any value, and pinning them here
        says that stays true if either is ever rewritten as a membership test
        the way ``origin`` already is.
        """
        unhashable_and_non_string = (
            [], ["external"], {"external": 1}, {"external"}, 7, None,
        )
        for key in ("origin", "source_strategy", "authority"):
            for value in unhashable_and_non_string:
                with self.subTest(key=key, value=value):
                    node = _tree_sitter_package("Newtonsoft.Json")
                    node["props"][key] = value
                    self.assertEqual(
                        emptied_tree_sitter_package_node_ids(
                            {"package:csharp:newtonsoft.json": node}, [],
                        ),
                        set(),
                    )


class EmptiedPlaceholderNodeIdsTreeSitterPackageTest(unittest.TestCase):
    """The union entry point :mod:`weld.discovery_state` actually calls."""

    def test_csharp_external_matches_the_union(self) -> None:
        nodes = {"package:csharp:newtonsoft.json": _tree_sitter_package("Newtonsoft.Json")}
        self.assertEqual(
            emptied_placeholder_node_ids(nodes, []), {"package:csharp:newtonsoft.json"},
        )

    def test_a_malformed_origin_is_total_across_the_whole_union(self) -> None:
        """Answering per-rule is not enough: this is the entry point
        ``purge_stale_nodes`` calls, and it runs EVERY rule over EVERY node,
        so a value this predicate now declines on safely could still raise --
        or be claimed -- inside a sibling rule (bd 5038-53jjg).
        """
        for value in ([], {"external"}, 7, None):
            with self.subTest(origin=value):
                node = _tree_sitter_package("Newtonsoft.Json")
                node["props"]["origin"] = value
                self.assertEqual(
                    emptied_placeholder_node_ids(
                        {"package:csharp:newtonsoft.json": node}, [],
                    ),
                    set(),
                )

    def test_java_unresolved_matches_the_union(self) -> None:
        nodes = {
            "package:java:com.fasterxml.jackson.databind": _tree_sitter_package(
                "com.fasterxml.jackson.databind", language="java", origin="unresolved",
            ),
        }
        self.assertEqual(
            emptied_placeholder_node_ids(nodes, []),
            {"package:java:com.fasterxml.jackson.databind"},
        )

    def test_csharp_project_origin_now_matches_the_union(self) -> None:
        nodes = {
            "package:csharp:myapp.deep.nested": _tree_sitter_package(
                "MyApp.Deep.Nested", origin="project",
            ),
        }
        self.assertEqual(
            emptied_placeholder_node_ids(nodes, []),
            {"package:csharp:myapp.deep.nested"},
        )

    def test_producer_shaped_node_matches_neither_package_rule(self) -> None:
        """A producer node matches neither this file's own edge-anchored
        rule (requires ``authority == "external"``; producer nodes are
        always ``"derived"``) nor the tree-sitter rule (requires
        ``source_strategy == "tree_sitter"``; producer nodes are
        ``"csharp_package"``) -- disjoint from both by construction. A live
        outgoing ``contains`` edge keeps g7rs's OWN membership rule (zero
        outgoing ``contains``, unrelated to either package rule) from also
        matching, so this isolates the two package rules specifically."""
        nodes = {"package:csharp:myapp.deep.nested": _producer_package("MyApp.Deep.Nested")}
        edges = [
            {
                "from": "package:csharp:myapp.deep.nested", "to": "file:member",
                "type": "contains", "props": {"source_strategy": "csharp_package"},
            },
        ]
        self.assertEqual(emptied_placeholder_node_ids(nodes, edges), set())


if __name__ == "__main__":
    unittest.main()
