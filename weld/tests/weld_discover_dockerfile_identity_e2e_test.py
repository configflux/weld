"""Every Dockerfile in a repo is its own node, end to end (bd bz5w9).

``weld.strategies.dockerfile`` derived its node id from the file *stem*
(``dockerfile:{df.stem.replace('.', '_')}``), so every ``Dockerfile`` in a
repository landed on the single id ``dockerfile:Dockerfile``. A monorepo with
one image per app got one node for all of them.

The survivor is not merely "the last one wins" -- it is a chimera, and that is
why this probe asserts per-node facts rather than a count alone. Measured on
the tree below before the fix, under both glob shapes:

    dockerfile nodes: ['dockerfile:Dockerfile']
    props.file:       apps/shop/Dockerfile      (the first file walked)
    base_image:       python:3.12-slim          (shop's)
    contains edges:   file:apps/blog/blog.js, file:apps/shop/shop.py

Node props are first-writer-wins (``weld._discover_node_merge``) while edges
accumulate, so one image's identity ends up wearing every image's source
closure. ``wd stats`` counts one dockerfile and the count looks plausible;
nothing warns.

This is the system-level probe, run through the real ``python -m weld`` in a
subprocess against a real git repo, because each layer looks fine on its own:
the walk finds both files, the inventory hashes both, the strategy reads both.
Only the whole run shows the two collapsing on the way into the node table.

Both glob shapes the issue names are exercised, as separate repos:
``apps/*/Dockerfile`` (the natural way to write it, resolvable since bd uhxjc)
and ``**/Dockerfile`` (which has always resolved, so the defect was reachable
before uhxjc too).

Assertions are keyed on ``props.file``, not on node ids: the claim under test
is "this repo has two images and the graph knows them apart", which is true
whatever the strategy spells its ids. The id *spelling* -- and the back-compat
promise that a lone root ``Dockerfile`` keeps ``dockerfile:Dockerfile`` -- is
pinned in ``weld_dockerfile_identity_test``, where it is legitimately the
subject.

``CMD`` is deliberately not asserted. The strategy surfaces ``FROM`` (as
``props.base_image``) and ``COPY``/``ADD`` (as ``contains`` edges); ``CMD`` is
not a graph fact today and inventing one here would be a feature, not a
regression probe. Each image's ``CMD`` names the entry script it ``COPY``s, so
the per-node ``contains`` edge below carries the same distinguishing evidence.
"""

from __future__ import annotations

import json
import unittest

from weld.tests._cli_e2e_harness import CliRepoHarness

#: Two apps, two images, two base images, two entry scripts. Distinct in every
#: observable respect so a collapsed node cannot look correct by coincidence.
TREE: dict[str, str] = {
    "apps/shop/Dockerfile": (
        "FROM python:3.12-slim\n"
        "COPY shop.py /app/shop.py\n"
        'CMD ["python", "/app/shop.py"]\n'
    ),
    "apps/shop/shop.py": "def shop():\n    return 'shop'\n",
    "apps/blog/Dockerfile": (
        "FROM node:20-alpine\n"
        "COPY blog.js /app/blog.js\n"
        'CMD ["node", "/app/blog.js"]\n'
    ),
    "apps/blog/blog.js": "export function blog() { return 'blog'; }\n",
}

#: ``props.file`` -> the base image that Dockerfile declares.
EXPECTED_BASE_IMAGES: dict[str, str] = {
    "apps/shop/Dockerfile": "python:3.12-slim",
    "apps/blog/Dockerfile": "node:20-alpine",
}

#: ``props.file`` -> the one ``file:`` node that Dockerfile ``COPY``s.
EXPECTED_CONTAINS: dict[str, str] = {
    "apps/shop/Dockerfile": "file:apps/shop/shop.py",
    "apps/blog/Dockerfile": "file:apps/blog/blog.js",
}

_CONFIG_TEMPLATE = """version: 1
sources:
  - glob: "{glob}"
    type: file
    strategy: dockerfile
"""


class _DockerfileIdentityCase:
    """One repo, one ``wd discover``, then the per-image assertions.

    A plain mixin rather than a ``TestCase`` subclass: an abstract base that
    inherits ``TestCase`` gets collected and reported as a skipped copy of
    every case below, which is noise in the one place a red probe's output has
    to be read carefully. Each concrete class sets :attr:`GLOB` and supplies
    the ``TestCase`` half.

    The temp git repo, the pinned environment and the "is this the checkout
    under test?" assertion come from ``weld.tests._cli_e2e_harness`` rather
    than being written out here again -- that module exists because two probes
    wrote the same six moves longhand within a day of each other.
    """

    #: The ``discover.yaml`` glob under test; set by each subclass.
    GLOB = ""

    graph: dict
    state: dict

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()  # type: ignore[misc]
        cls.setup_cli_repo(  # type: ignore[attr-defined]
            TREE, _CONFIG_TEMPLATE.format(glob=cls.GLOB),
        )
        cls.wd("discover", "--output", ".weld/graph.json")  # type: ignore[attr-defined]
        cls.graph = cls.read_json(".weld/graph.json")  # type: ignore[attr-defined]
        cls.state = cls.read_json(  # type: ignore[attr-defined]
            ".weld/discovery-state.json",
        )

    # -- helpers ---------------------------------------------------------

    def _dockerfile_nodes(self) -> dict[str, dict]:
        """Every ``type: dockerfile`` node, keyed by node id."""
        return {
            nid: node
            for nid, node in self.graph.get("nodes", {}).items()
            if node.get("type") == "dockerfile"
        }

    def _nodes_by_source_file(self) -> dict[str, tuple[str, dict]]:
        """``props.file`` -> (node id, node) for the dockerfile nodes."""
        out: dict[str, tuple[str, dict]] = {}
        for nid, node in self._dockerfile_nodes().items():
            rel = (node.get("props") or {}).get("file")
            if isinstance(rel, str):
                out[rel] = (nid, node)
        return out

    def _contains_targets(self, nid: str) -> set[str]:
        return {
            edge["to"]
            for edge in self.graph.get("edges", [])
            if edge.get("from") == nid and edge.get("type") == "contains"
        }

    # -- vacuity floor ---------------------------------------------------

    def test_discovery_read_both_dockerfiles(self) -> None:
        """Guard: the glob must have matched both files in the first place.

        Without this, a glob regression that matched only one file would fail
        the identity assertions below and be mistaken for the identity defect.
        Provenance is ADR 0017's record of what discovery actually read.
        """
        discovered_from = set(self.graph.get("meta", {}).get(
            "discovered_from", []
        ))
        for rel in EXPECTED_BASE_IMAGES:
            with self.subTest(file=rel):
                self.assertIn(rel, discovered_from)
        inventory = set(self.state.get("files", {}))
        for rel in EXPECTED_BASE_IMAGES:
            with self.subTest(file=rel, where="inventory"):
                self.assertIn(rel, inventory)

    # -- the defect ------------------------------------------------------

    def test_each_dockerfile_gets_its_own_node(self) -> None:
        """The headline: two images in the repo, two nodes in the graph."""
        nodes = self._dockerfile_nodes()
        by_file = self._nodes_by_source_file()
        self.assertEqual(
            sorted(by_file), sorted(EXPECTED_BASE_IMAGES),
            "every Dockerfile in the repo collapsed onto one node; "
            f"ids emitted: {sorted(nodes)}, "
            f"source files they name: {sorted(by_file)}",
        )
        self.assertEqual(
            len(nodes), len(EXPECTED_BASE_IMAGES),
            f"expected one node per Dockerfile; got {sorted(nodes)}",
        )

    def test_each_node_carries_its_own_base_image(self) -> None:
        """``FROM`` is per image, so ``props.base_image`` must be too.

        The collapsed node kept the first-walked file's ``base_image`` and
        presented it as the base image of every image in the repo.
        """
        by_file = self._nodes_by_source_file()
        for rel, expected in EXPECTED_BASE_IMAGES.items():
            with self.subTest(file=rel):
                self.assertIn(rel, by_file)
                _, node = by_file[rel]
                self.assertEqual(
                    (node.get("props") or {}).get("base_image"), expected,
                )

    def test_each_node_carries_only_its_own_contains_edges(self) -> None:
        """The ``COPY`` closure of one image must not be attributed to another.

        Both halves matter: the node must reach its own source (``assertIn``)
        and must *not* reach its sibling's (``assertNotIn``). The collapsed
        node satisfied the first half for both images while being wrong.
        """
        by_file = self._nodes_by_source_file()
        for rel, own_target in EXPECTED_CONTAINS.items():
            with self.subTest(file=rel):
                self.assertIn(rel, by_file)
                nid, _ = by_file[rel]
                targets = self._contains_targets(nid)
                self.assertIn(own_target, targets)
                foreign = [
                    other for other_rel, other in EXPECTED_CONTAINS.items()
                    if other_rel != rel and other in targets
                ]
                self.assertEqual(
                    foreign, [],
                    f"{nid} claims another image's COPY sources: {foreign}",
                )

    def test_stats_counts_one_dockerfile_per_dockerfile(self) -> None:
        """The user-visible symptom: ``wd stats`` under-counts, plausibly.

        Asserted through the CLI rather than by re-counting ``graph.json``, so
        the number a user actually reads is the one under test.
        """
        stats = json.loads(self.wd("stats", "--json").stdout)
        by_type = stats.get("nodes_by_type") or {}
        self.assertEqual(
            by_type.get("dockerfile"), len(EXPECTED_BASE_IMAGES),
            f"`wd stats` reports {by_type.get('dockerfile')} dockerfile "
            f"node(s) for a repo with {len(EXPECTED_BASE_IMAGES)} Dockerfiles",
        )


class SegmentGlobDockerfileIdentityTest(
    _DockerfileIdentityCase, CliRepoHarness, unittest.TestCase,
):
    """``apps/*/Dockerfile`` -- the natural way to write it (bd uhxjc)."""

    GLOB = "apps/*/Dockerfile"


class RecursiveGlobDockerfileIdentityTest(
    _DockerfileIdentityCase, CliRepoHarness, unittest.TestCase,
):
    """``**/Dockerfile`` -- always resolved, so the defect predates uhxjc."""

    GLOB = "**/Dockerfile"


if __name__ == "__main__":
    unittest.main()
