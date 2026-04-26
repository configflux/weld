"""Authority-gated suppression bridge tests for render pair helpers.

ADR 0029 (audit suppression for canonical+rendered pairs) bridges
``generated_from`` edges with ``render_paths`` claims. A non-canonical
node that *claims* to render another asset must NOT contribute to that
bridge -- otherwise a same-name agent could mask its own
``duplicate_name`` finding by adding a fake ``render_paths`` prop.

These tests pin the authority gate at both the helper layer and the
``wd agents audit`` CLI layer."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Iterator

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.agent_graph_render_pairs import (  # noqa: E402
    render_pair_links, render_pair_partners,
)
from weld.cli import main as wd_main  # noqa: E402


@contextmanager
def _cwd(path: Path) -> Iterator[None]:
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _run(argv: list[str], root: Path) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with _cwd(root), redirect_stdout(out), redirect_stderr(err):
        rc = wd_main(argv)
    return rc, out.getvalue(), err.getvalue()


def _write(root: Path, rel_path: str, text: str) -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _asset_node(node_id: str, file_path: str, **extra_props: object) -> dict:
    props = {
        "file": file_path,
        "source_strategy": "agent_graph_static",
        **extra_props,
    }
    return {"id": node_id, "type": "agent", "props": props}


class RenderPairLinksAuthorityGateTest(unittest.TestCase):
    """``render_paths`` claims only count when the declaring node is
    canonical. Anything else (``derived``, ``manual``, missing) is
    ignored for bridge purposes."""

    def _graph(self, declaring_authority: object) -> dict:
        canonical = _asset_node(
            "n.canonical",
            ".github/agents/planner.agent.md",
            authority="canonical",
            authority_name="planner",
        )
        rendered = _asset_node(
            "n.rendered",
            ".claude/agents/planner.md",
            authority="derived",
            authority_name="planner",
        )
        attacker_props: dict[str, object] = {
            "render_paths": [".claude/agents/planner.md"],
        }
        if declaring_authority is not None:
            attacker_props["authority"] = declaring_authority
        attacker = _asset_node(
            "n.attacker",
            ".github/agents/attacker.agent.md",
            **attacker_props,
        )
        return {
            "nodes": {
                "n.canonical": canonical,
                "n.rendered": rendered,
                "n.attacker": attacker,
            },
            "edges": [],
        }

    def test_non_canonical_render_paths_does_not_bridge(self) -> None:
        for label in ("derived", "manual", "rendered", "true_string_typo", None):
            with self.subTest(authority=label):
                graph = self._graph(label)
                links = render_pair_links(graph)
                self.assertEqual(
                    links,
                    {},
                    msg=f"non-canonical authority={label!r} must not bridge",
                )

    def test_canonical_render_paths_bridges(self) -> None:
        graph = self._graph("canonical")
        # Make the attacker the genuine canonical declaring the rendered target
        # by re-pointing render_paths from the actual canonical node.
        graph["nodes"]["n.canonical"]["props"]["render_paths"] = [
            ".claude/agents/planner.md",
        ]
        # Strip the imitator's claim now that the real canonical declares it.
        graph["nodes"]["n.attacker"]["props"].pop("render_paths", None)
        graph["nodes"]["n.attacker"]["props"]["authority"] = "manual"
        links = render_pair_links(graph)
        self.assertEqual(
            links,
            {"n.canonical": {"n.rendered"}, "n.rendered": {"n.canonical"}},
        )

    def test_authority_true_value_is_treated_as_canonical(self) -> None:
        """``authority: true`` in agent frontmatter is normalized to
        ``canonical`` semantics by ``asset_status``; the bridge must
        accept it as well."""
        graph = self._graph(None)
        graph["nodes"]["n.canonical"]["props"]["render_paths"] = [
            ".claude/agents/planner.md",
        ]
        graph["nodes"]["n.canonical"]["props"]["authority"] = True
        graph["nodes"]["n.attacker"]["props"].pop("render_paths", None)
        links = render_pair_links(graph)
        self.assertIn("n.rendered", links.get("n.canonical", set()))
        self.assertIn("n.canonical", links.get("n.rendered", set()))

    def test_explicit_generated_from_edge_still_bridges(self) -> None:
        """The authority gate is on ``render_paths`` only -- explicit
        ``generated_from`` edges from discover are independent provenance
        and continue to bridge regardless of declaring-node authority."""
        graph = self._graph("derived")  # attacker still has fake claim
        graph["edges"].append({
            "from": "n.rendered",
            "to": "n.canonical",
            "type": "generated_from",
        })
        links = render_pair_links(graph)
        self.assertEqual(links["n.canonical"], {"n.rendered"})
        self.assertEqual(links["n.rendered"], {"n.canonical"})
        # And the attacker stays unconnected.
        self.assertNotIn("n.attacker", links)


class RenderPairPartnersAuthorityGateTest(unittest.TestCase):
    """``render_pair_partners`` is the explain-side analog of
    ``render_pair_links`` and shares the same trust boundary."""

    def _graph_with_attacker(self) -> dict:
        canonical = _asset_node(
            "n.canonical",
            ".github/agents/planner.agent.md",
            authority="canonical",
            authority_name="planner",
        )
        rendered = _asset_node(
            "n.rendered",
            ".claude/agents/planner.md",
            authority="derived",
            authority_name="planner",
        )
        attacker = _asset_node(
            "n.attacker",
            ".github/agents/attacker.agent.md",
            authority="manual",
            render_paths=[".claude/agents/planner.md"],
        )
        return {
            "nodes": {
                "n.canonical": canonical,
                "n.rendered": rendered,
                "n.attacker": attacker,
            },
            "edges": [],
        }

    def test_partners_ignore_non_canonical_render_paths_for_target(
        self,
    ) -> None:
        """Looking up partners for the *attacker*: even though the
        attacker's own props declare render_paths, it is non-canonical and
        must not gain rendered partners through that claim."""
        graph = self._graph_with_attacker()
        canonicals, rendered = render_pair_partners(
            graph, "n.attacker", ".github/agents/attacker.agent.md",
        )
        self.assertEqual(canonicals, [])
        self.assertEqual(rendered, [])

    def test_partners_ignore_non_canonical_claims_against_target(
        self,
    ) -> None:
        """Looking up partners for the *legitimate rendered* node: the
        attacker's claim to render it must not surface the attacker as a
        canonical source."""
        graph = self._graph_with_attacker()
        canonicals, rendered = render_pair_partners(
            graph, "n.rendered", ".claude/agents/planner.md",
        )
        self.assertEqual(canonicals, [])
        self.assertEqual(rendered, [])

    def test_partners_ignore_non_canonical_target_with_render_claim(
        self,
    ) -> None:
        """Looking up partners for the *legitimate canonical*: a
        non-canonical node that *claims* the canonical's path as its
        canonical-path (impersonation) must not flip the canonical into
        a rendered partner of the attacker."""
        graph = self._graph_with_attacker()
        # Add an impersonator: a non-canonical node whose ``file`` matches
        # the canonical's path AND who claims to render the legitimate
        # rendered target. None of this should add partners.
        graph["nodes"]["n.impersonator"] = _asset_node(
            "n.impersonator",
            ".github/agents/planner.agent.md",  # same path as canonical
            authority="derived",
            render_paths=[".claude/agents/planner.md"],
        )
        canonicals, rendered = render_pair_partners(
            graph, "n.canonical", ".github/agents/planner.agent.md",
        )
        # The legitimate canonical has no render_paths of its own and no
        # generated_from edges, so it has no partners. The impersonator's
        # claim must NOT add the rendered node as a partner of the
        # canonical.
        self.assertEqual(canonicals, [])
        self.assertEqual(rendered, [])

    def test_partners_canonical_with_render_paths_still_works(
        self,
    ) -> None:
        """The legitimate path: a canonical node declaring ``render_paths``
        gains its rendered targets as partners."""
        graph = self._graph_with_attacker()
        graph["nodes"]["n.attacker"]["props"].pop("render_paths", None)
        graph["nodes"]["n.canonical"]["props"]["render_paths"] = [
            ".claude/agents/planner.md",
        ]
        canonicals, rendered = render_pair_partners(
            graph, "n.canonical", ".github/agents/planner.agent.md",
        )
        self.assertEqual(canonicals, [])
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0]["node"]["id"], "n.rendered")


class AuditCliNonCanonicalRenderPathsClaimTest(unittest.TestCase):
    """End-to-end pin: ``wd agents audit`` must surface a
    ``duplicate_name`` finding when the only thing connecting two
    same-name agents is a non-canonical node forging a ``render_paths``
    declaration. This is the exact attack the trust-boundary tightening
    closes (security follow-up of bd-88ey)."""

    def test_non_canonical_render_paths_claim_does_not_suppress(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".github/agents/planner.agent.md",
                "---\nname: planner\ndescription: Plans implementation.\n---\n",
            )
            _write(
                root,
                ".claude/agents/planner.md",
                "---\nname: planner\ndescription: Drafts implementation.\n---\n",
            )
            self.assertEqual(_run(["agents", "discover"], root)[0], 0)
            graph_path = root / ".weld" / "agent-graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            planner_ids = [
                node_id for node_id, node in graph["nodes"].items()
                if node.get("type") == "agent"
                and isinstance(node.get("props"), dict)
                and node["props"].get("name") == "planner"
            ]
            self.assertEqual(len(planner_ids), 2, msg=planner_ids)
            # Inject a forged render_paths claim on the .github planner
            # without marking it canonical. A correct implementation
            # ignores the claim and surfaces the duplicate_name finding.
            for node_id in planner_ids:
                props = graph["nodes"][node_id]["props"]
                if ".github/agents/planner.agent.md" in str(
                    props.get("file") or "",
                ):
                    props["render_paths"] = [".claude/agents/planner.md"]
                    self.assertNotIn(
                        props.get("authority"), ("canonical", True), msg=props,
                    )
            graph_path.write_text(
                json.dumps(graph, sort_keys=True), encoding="utf-8",
            )
            rc, stdout, stderr = _run(["agents", "audit", "--json"], root)
            self.assertEqual((rc, stderr), (0, ""))
            findings = json.loads(stdout)["findings"]
            duplicates = [
                f for f in findings
                if f["code"] == "duplicate_name"
                and any(n["name"] == "planner" for n in f["nodes"])
            ]
            self.assertEqual(len(duplicates), 1, msg=findings)


if __name__ == "__main__":
    unittest.main()
