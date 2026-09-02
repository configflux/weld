"""Tests for the Dockerfile strategy: COPY/ADD source-edge extraction.

Layer C2 (ADR 0045) added ``contains`` edges from each ``dockerfile:*``
node to the ``file:*`` source paths it COPYs / ADDs. Multi-stage
``COPY --from=...`` instructions and URL ``ADD https://...`` instructions
are explicitly skipped, with counters surfaced on the dockerfile node's
props so consumers can see the overapproximation.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.dockerfile import extract


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


class DockerfileCopySimpleTest(unittest.TestCase):
    """COPY/ADD with literal repo-relative source paths."""

    def test_emits_contains_edge_for_each_copy_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "Dockerfile", (
                "FROM python:3.12-slim\n"
                "COPY requirements.txt /app/\n"
                "COPY ./src /app/src\n"
                "ADD config.yaml /etc/app/config.yaml\n"
            ))
            _write(root / "requirements.txt", "weld\n")
            (root / "src").mkdir()
            _write(root / "config.yaml", "k: v\n")

            result = extract(root, {"glob": "Dockerfile"}, {})

            self.assertIsInstance(result, StrategyResult)
            df_id = "dockerfile:Dockerfile"
            self.assertIn(df_id, result.nodes)
            contains = [
                e for e in result.edges
                if e["from"] == df_id and e["type"] == "contains"
            ]
            tos = sorted(e["to"] for e in contains)
            self.assertIn("file:requirements.txt", tos)
            self.assertIn("file:src", tos)
            self.assertIn("file:config.yaml", tos)
            for edge in contains:
                props = edge.get("props", {})
                self.assertEqual(props.get("source_strategy"), "dockerfile")
                self.assertEqual(props.get("confidence"), "definite")
            # Each contains-edge target must also exist as a node,
            # otherwise ``_clean_and_dedup_edges`` would prune the edge
            # in the real ``wd discover`` pipeline (Layer C2 fix-forward).
            for edge in contains:
                self.assertIn(edge["to"], result.nodes)
                fnode = result.nodes[edge["to"]]
                self.assertEqual(fnode["type"], "file")
                self.assertEqual(
                    fnode["props"].get("source_strategy"), "dockerfile",
                )
                self.assertIn(
                    "build", fnode["props"].get("roles", []),
                )

    def test_dockerfile_in_subdir_resolves_relative_to_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "services" / "api" / "Dockerfile", (
                "FROM python:3.12-slim\n"
                "COPY app.py /app/\n"
                "COPY ../shared /app/shared\n"
            ))
            _write(root / "services" / "api" / "app.py", "x = 1\n")
            (root / "services" / "shared").mkdir()

            result = extract(
                root, {"glob": "services/api/Dockerfile"}, {},
            )
            # Keyed on the Dockerfile's own repo-relative path, not its
            # stem -- the identity contract pinned in
            # ``weld_dockerfile_identity_test`` (bd bz5w9).
            df_id = "dockerfile:services/api/Dockerfile"
            tos = sorted(
                e["to"] for e in result.edges
                if e["from"] == df_id and e["type"] == "contains"
            )
            self.assertIn("file:services/api/app.py", tos)
            self.assertIn("file:services/shared", tos)


class DockerfileCopyEdgeCasesTest(unittest.TestCase):
    """Multi-stage, URL ADD, glob: counters reflect the skips."""

    def test_multistage_copy_from_skipped_with_counter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "Dockerfile", (
                "FROM golang:1.22 AS builder\n"
                "COPY main.go /src/main.go\n"
                "FROM gcr.io/distroless/base\n"
                "COPY --from=builder /src/app /app/app\n"
                "COPY --from=builder /src/extra /app/extra\n"
            ))
            _write(root / "main.go", "package main\n")

            result = extract(root, {"glob": "Dockerfile"}, {})
            df_id = "dockerfile:Dockerfile"
            props = result.nodes[df_id]["props"]
            self.assertEqual(props.get("multistage_skipped"), 2)
            tos = sorted(
                e["to"] for e in result.edges
                if e["from"] == df_id and e["type"] == "contains"
            )
            self.assertEqual(tos, ["file:main.go"])

    def test_url_add_skipped_silently(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "Dockerfile", (
                "FROM alpine:3.20\n"
                "ADD https://example.com/foo.tar.gz /opt/foo.tar.gz\n"
                "ADD http://example.com/bar /opt/bar\n"
                "COPY local.txt /opt/local.txt\n"
            ))
            _write(root / "local.txt", "x\n")

            result = extract(root, {"glob": "Dockerfile"}, {})
            df_id = "dockerfile:Dockerfile"
            tos = [
                e["to"] for e in result.edges
                if e["from"] == df_id and e["type"] == "contains"
            ]
            self.assertEqual(tos, ["file:local.txt"])
            # URL skips are not counted: out of scope for repo-relative
            # source resolution.
            self.assertNotIn("url_skipped", result.nodes[df_id]["props"])

    def test_glob_counted_but_no_edge_emitted(self) -> None:
        # ADR 0045 / C2 fix-forward: glob sources are over-approximate
        # by construction (no on-disk expansion happens here), so the
        # strategy bumps ``glob_count`` for visibility but does NOT
        # emit a ``contains`` edge. A literal-pattern target like
        # ``file:*.py`` is not a real path and would be pruned by
        # ``_clean_and_dedup_edges`` anyway; emitting it created a
        # phantom edge in pre-fix runs.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "Dockerfile", (
                "FROM python:3.12-slim\n"
                "COPY *.py /app/\n"
                "COPY pkg/**/*.py /app/pkg/\n"
            ))
            _write(root / "x.py", "y = 1\n")

            result = extract(root, {"glob": "Dockerfile"}, {})
            df_id = "dockerfile:Dockerfile"
            props = result.nodes[df_id]["props"]
            self.assertEqual(props.get("glob_count"), 2)
            tos = sorted(
                e["to"] for e in result.edges
                if e["from"] == df_id and e["type"] == "contains"
            )
            self.assertEqual(tos, [])
            # And no phantom file: nodes for the glob patterns either.
            self.assertNotIn("file:*.py", result.nodes)
            self.assertNotIn("file:pkg/**/*.py", result.nodes)


class DockerfileCopyDirectoryWalkTest(unittest.TestCase):
    """COPY targeting a directory bridges to interior files.

    Pins the Layer C2 directory-walk behaviour: ``COPY ./app /...`` must
    emit ``file:app -> file:app/<child>`` edges for every interior file
    so reverse-BFS from any interior file reaches the dockerfile.
    """

    def test_directory_copy_emits_contains_edges_for_interior_files(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "Dockerfile", (
                "FROM python:3.12-slim\n"
                "COPY ./app /service/app\n"
            ))
            _write(root / "app" / "lib.py", "x = 1\n")
            _write(root / "app" / "main.py", "y = 2\n")
            _write(root / "app" / "sub" / "deep.py", "z = 3\n")

            result = extract(root, {"glob": "Dockerfile"}, {})

            df_id = "dockerfile:Dockerfile"
            self.assertIn(df_id, result.nodes)
            # Outer contains-edge from dockerfile to the directory file
            # node still emitted (existing behaviour).
            outer = [
                e for e in result.edges
                if e["from"] == df_id and e["type"] == "contains"
            ]
            self.assertEqual(
                sorted(e["to"] for e in outer), ["file:app"],
            )
            # New: directory-walk contains-edges from file:app to each
            # interior file. Recursive: nested files reached too.
            interior = sorted(
                e["to"] for e in result.edges
                if e["from"] == "file:app" and e["type"] == "contains"
            )
            self.assertEqual(
                interior,
                [
                    "file:app/lib.py",
                    "file:app/main.py",
                    "file:app/sub/deep.py",
                ],
            )
            # Each child has a node + ``source_strategy=dockerfile`` and
            # the build role, mirroring the outer contains-edge contract.
            for target in interior:
                self.assertIn(target, result.nodes)
                fnode = result.nodes[target]
                self.assertEqual(fnode["type"], "file")
                self.assertEqual(
                    fnode["props"].get("source_strategy"), "dockerfile",
                )
                self.assertIn(
                    "build", fnode["props"].get("roles", []),
                )

    def test_single_file_copy_unchanged_regression(self) -> None:
        # Regression: COPY of a single file must NOT trigger walk edges.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "Dockerfile", (
                "FROM python:3.12-slim\n"
                "COPY app/lib.py /service/app/lib.py\n"
            ))
            _write(root / "app" / "lib.py", "x = 1\n")
            _write(root / "app" / "main.py", "y = 2\n")  # sibling

            result = extract(root, {"glob": "Dockerfile"}, {})

            df_id = "dockerfile:Dockerfile"
            outer = sorted(
                e["to"] for e in result.edges
                if e["from"] == df_id and e["type"] == "contains"
            )
            self.assertEqual(outer, ["file:app/lib.py"])
            # No directory-walk edge from any file: node (the source
            # token resolves to a file, not a directory, so no walk).
            walk = [
                e for e in result.edges
                if e["from"].startswith("file:")
                and e["type"] == "contains"
            ]
            self.assertEqual(walk, [])

    def test_directory_copy_when_dir_missing_no_walk(self) -> None:
        # Missing dir: outer edge lands; walk contributes nothing.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "Dockerfile", (
                "FROM python:3.12-slim\n"
                "COPY ./app /service/app\n"
            ))
            # No app/ directory exists.

            result = extract(root, {"glob": "Dockerfile"}, {})

            df_id = "dockerfile:Dockerfile"
            outer = sorted(
                e["to"] for e in result.edges
                if e["from"] == df_id and e["type"] == "contains"
            )
            self.assertEqual(outer, ["file:app"])
            walk = [
                e for e in result.edges
                if e["from"] == "file:app" and e["type"] == "contains"
            ]
            self.assertEqual(walk, [])

    def test_root_copy_is_skipped_with_counter(self) -> None:
        # ``COPY . /app/`` resolves to the discovery root; walking it
        # would balloon contains-edges to every file in the repo. Drop
        # the walk and bump ``dir_walk_skipped`` for visibility.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "Dockerfile", (
                "FROM python:3.12-slim\n"
                "COPY . /app/\n"
            ))
            _write(root / "a.py", "x = 1\n")
            _write(root / "b.py", "y = 2\n")

            result = extract(root, {"glob": "Dockerfile"}, {})

            df_id = "dockerfile:Dockerfile"
            props = result.nodes[df_id]["props"]
            self.assertEqual(props.get("dir_walk_skipped"), 1)
            # No walk edges for the root case.
            walk = [
                e for e in result.edges
                if e["from"].startswith("file:")
                and e["type"] == "contains"
            ]
            self.assertEqual(walk, [])

    def test_directory_copy_skips_excluded_dir_names(self) -> None:
        # Walk honours the global exclusion policy (``.git``,
        # ``node_modules``, ``__pycache__``) so vendored trees don't
        # explode the contains-edge count.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "Dockerfile", (
                "FROM python:3.12-slim\n"
                "COPY ./app /service/app\n"
            ))
            _write(root / "app" / "lib.py", "x = 1\n")
            _write(root / "app" / "node_modules" / "pkg" / "i.js", "")
            _write(root / "app" / "__pycache__" / "lib.cpython.pyc", "")

            result = extract(root, {"glob": "Dockerfile"}, {})

            interior = sorted(
                e["to"] for e in result.edges
                if e["from"] == "file:app" and e["type"] == "contains"
            )
            self.assertEqual(interior, ["file:app/lib.py"])

    def test_directory_walk_is_deterministic(self) -> None:
        # Walk uses sorted(); pin it so a regression to filesystem
        # iteration order fails loudly.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "Dockerfile", (
                "FROM python:3.12-slim\n"
                "COPY ./app /service/app\n"
            ))
            for n in ("zebra.py", "apple.py", "mango.py"):
                _write(root / "app" / n, "x = 1\n")

            r1 = extract(root, {"glob": "Dockerfile"}, {})
            r2 = extract(root, {"glob": "Dockerfile"}, {})
            tos1 = [
                e["to"] for e in r1.edges
                if e["from"] == "file:app" and e["type"] == "contains"
            ]
            tos2 = [
                e["to"] for e in r2.edges
                if e["from"] == "file:app" and e["type"] == "contains"
            ]
            self.assertEqual(tos1, tos2)
            # And alphabetical for predictability across runs.
            self.assertEqual(tos1, sorted(tos1))


class DockerfileDeterminismTest(unittest.TestCase):
    def test_edge_order_is_deterministic_per_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(root / "Dockerfile", (
                "FROM alpine\n"
                "COPY zebra.txt /a/zebra.txt\n"
                "COPY apple.txt /a/apple.txt\n"
                "COPY mango.txt /a/mango.txt\n"
            ))
            for n in ("zebra.txt", "apple.txt", "mango.txt"):
                _write(root / n, "x\n")

            r1 = extract(root, {"glob": "Dockerfile"}, {})
            r2 = extract(root, {"glob": "Dockerfile"}, {})
            df_id = "dockerfile:Dockerfile"
            tos1 = [
                e["to"] for e in r1.edges
                if e["from"] == df_id and e["type"] == "contains"
            ]
            tos2 = [
                e["to"] for e in r2.edges
                if e["from"] == df_id and e["type"] == "contains"
            ]
            self.assertEqual(tos1, tos2)


if __name__ == "__main__":
    unittest.main()
