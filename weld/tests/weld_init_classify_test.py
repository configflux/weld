"""Unit tests for the single-pass init classifier (ADR 0027 follow-up).

The classifier centralises every per-file ``relative_to`` walk that the
``detect_*`` helpers used to do independently. These tests pin both
sides:

* ``classify_files`` populates each aggregate field correctly for the
  shapes ``wd init`` cares about (root configs, Dockerfile, compose,
  CI, Claude agents/commands, monorepo tops, doc dirs, python dirs).
* ``detect_all_from_classified`` returns the same values that the
  individual ``detect_*`` public helpers would have returned for the
  same input. This is the regression net protecting the orchestrator
  fast path against silent drift.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from weld._init_classify import classify_files  # noqa: E402
from weld.init_detect import (  # noqa: E402
    detect_all_from_classified,
    detect_ci,
    detect_claude,
    detect_compose,
    detect_dockerfiles,
    detect_docs,
    detect_root_configs,
    detect_structure,
    find_python_glob_roots,
    scan_files,
)


def _build_repo(td: str) -> Path:
    """Create a fixture repo that exercises every classifier field."""
    root = Path(td)
    # Root-level recognised configs, Dockerfile, compose, root .py file.
    (root / "pyproject.toml").write_text("[tool]\n")
    (root / "Makefile").write_text("all:\n")
    (root / "Dockerfile").write_text("FROM scratch\n")
    (root / "docker-compose.yml").write_text("services: {}\n")
    (root / "main.py").write_text("x = 1\n")
    # docker/ multiple Dockerfiles
    docker = root / "docker"
    docker.mkdir()
    (docker / "api.Dockerfile").write_text("FROM scratch\n")
    (docker / "worker.Dockerfile").write_text("FROM scratch\n")
    # Monorepo top dirs and Python sources beneath them
    (root / "services" / "api").mkdir(parents=True)
    (root / "services" / "api" / "app.py").write_text("from fastapi import x\n")
    (root / "libs" / "models").mkdir(parents=True)
    (root / "libs" / "models" / "user.py").write_text("y = 2\n")
    # docs/ dir
    (root / "docs").mkdir()
    (root / "docs" / "index.md").write_text("# docs\n")
    # CI workflows
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("name: ci\n")
    (wf / "release.yaml").write_text("name: release\n")
    # Claude detection is exercised by the acceptance suite via real
    # fixtures; .claude/ is in SKIP_DIRS for the os.walk fallback and
    # never reaches the classifier under tempfile-only tests, so we
    # exercise it via direct classify_files of synthetic Path objects
    # rather than scan_files on disk.
    return root


class ClassifyFilesAggregatesTest(unittest.TestCase):
    """``classify_files`` populates every aggregate the detectors read."""

    def test_classify_populates_every_field(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _build_repo(td)
            files = scan_files(root)
            c = classify_files(root, files)

        self.assertGreater(len(c.files), 0)
        # Every record carries the precomputed shape.
        for cf in c.files:
            self.assertEqual(cf.parts, cf.path.relative_to(root).parts)
            self.assertEqual(cf.suffix, cf.path.suffix.lower())
            self.assertEqual(cf.name, cf.path.name)
        # Aggregates the detectors will read.
        self.assertIn("services", c.monorepo_tops_seen)
        self.assertIn("libs", c.monorepo_tops_seen)
        self.assertIn("docs", c.doc_dirs_seen)
        self.assertTrue(c.has_root_dockerfile)
        self.assertEqual(sorted(c.compose_files), ["docker-compose.yml"])
        self.assertEqual(sorted(c.ci_files), ["ci.yml", "release.yaml"])
        self.assertTrue(c.has_root_py)
        # Two docker/* Dockerfiles registered at posix paths.
        self.assertEqual(
            sorted(c.docker_dir_files),
            ["docker/api.Dockerfile", "docker/worker.Dockerfile"],
        )
        # Python dirs include the monorepo subdirectories.
        self.assertIn("services/api", c.py_dirs)
        self.assertIn("libs/models", c.py_dirs)
        # Recognised root configs only.
        self.assertEqual(
            c.root_config_names,
            {"pyproject.toml", "Makefile"},
        )

    def test_classify_recognises_claude_agents_and_commands(self) -> None:
        """``.claude/`` is in SKIP_DIRS for the os.walk fallback so it
        never reaches scan_files in tempfile tests; feed the classifier
        synthetic paths to pin the matching condition itself."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = [
                root / ".claude" / "agents" / "tdd.md",
                root / ".claude" / "agents" / "qa.md",
                root / ".claude" / "commands" / "execute.md",
                root / ".claude" / "commands" / "extra" / "deep.md",
                root / ".claude" / "agents" / "shouldskip.txt",
            ]
            for p in paths:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("x", encoding="utf-8")
            c = classify_files(root, paths)
        self.assertEqual(sorted(c.claude_agents), ["qa.md", "tdd.md"])
        self.assertEqual(c.claude_commands, ["execute.md"])

    def test_classify_handles_empty_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            files = scan_files(root)
            c = classify_files(root, files)
        self.assertEqual(c.files, [])
        self.assertFalse(c.has_root_dockerfile)
        self.assertFalse(c.has_root_py)
        self.assertEqual(c.compose_files, [])
        self.assertEqual(c.ci_files, [])
        self.assertEqual(c.claude_agents, [])
        self.assertEqual(c.claude_commands, [])


class DetectAllParityTest(unittest.TestCase):
    """``detect_all_from_classified`` matches the public detect_* APIs."""

    def _expected_for_root(self, root: Path) -> dict:
        files = scan_files(root)
        agents, commands = detect_claude(root, files)
        return {
            "structure": detect_structure(root, files),
            "dockerfiles": detect_dockerfiles(root, files),
            "compose_files": detect_compose(root, files),
            "ci_files": detect_ci(root, files),
            "claude_agents": agents,
            "claude_commands": commands,
            "doc_dirs": detect_docs(root, files),
            "python_globs": find_python_glob_roots(root, files),
            "root_configs": detect_root_configs(root, files),
        }

    def test_detect_all_matches_individual_detectors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = _build_repo(td)
            files = scan_files(root)
            classified = classify_files(root, files)
            actual = detect_all_from_classified(classified)
            expected = self._expected_for_root(root)
        # detect_dockerfiles preserves a stable ordering when the docker
        # directory has multiple .Dockerfile entries; a sorted compare
        # makes the parity check insensitive to in-classifier append
        # order while still pinning content.
        self.assertEqual(sorted(actual["dockerfiles"]),
                         sorted(expected["dockerfiles"]))
        self.assertEqual(sorted(actual["ci_files"]),
                         sorted(expected["ci_files"]))
        self.assertEqual(sorted(actual["compose_files"]),
                         sorted(expected["compose_files"]))
        # The remaining fields must be exactly equal.
        for key in (
            "structure", "claude_agents", "claude_commands",
            "doc_dirs", "python_globs", "root_configs",
        ):
            self.assertEqual(
                actual[key], expected[key],
                f"parity mismatch on {key}: "
                f"{actual[key]!r} vs {expected[key]!r}",
            )

    def test_detect_all_on_minimal_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tool.py").write_text("x = 1\n")
            files = scan_files(root)
            classified = classify_files(root, files)
            actual = detect_all_from_classified(classified)
            expected = self._expected_for_root(root)
        self.assertEqual(actual["structure"], expected["structure"])
        self.assertEqual(actual["doc_dirs"], expected["doc_dirs"])
        self.assertEqual(actual["python_globs"], expected["python_globs"])


class RootConfigEcosystemManifestTest(unittest.TestCase):
    """Every ecosystem weld extracts from has its manifest claimed.

    ``ROOT_CONFIG_NAMES`` is what turns a repo's manifests into ``config:``
    nodes. ``tsconfig.json`` was missing from it while every other
    ecosystem's manifest was present, so a TypeScript project's compiler
    config -- the file weld itself reads for ``compilerOptions.paths``
    alias resolution -- never became a node in that project's graph
    (bd 5038-q9hba). The gap survived because the one gold set that
    asserted the node was authored against a hand-written discover.yaml
    that *did* claim it, while the gate that consumed the gold rebuilt its
    config with ``wd init``: nothing compared the two.
    """

    def _detect(self, names: tuple[str, ...]) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n")
            return detect_root_configs(root, scan_files(root))

    def test_root_manifests_are_detected(self) -> None:
        """One manifest per ecosystem, each claimed at the repo root."""
        manifests = (
            "pyproject.toml", "package.json", "tsconfig.json",
            "Cargo.toml", "go.mod", "Makefile", "MODULE.bazel",
        )
        detected = self._detect(manifests)
        for name in manifests:
            with self.subTest(manifest=name):
                self.assertIn(
                    name, detected,
                    f"{name} is the canonical manifest of an ecosystem weld "
                    f"extracts from and must be claimed as a root config; "
                    f"detected={detected}",
                )

    def test_nested_tsconfig_does_not_fire(self) -> None:
        """Root-only, like every other entry: a nested copy is not a root
        config. A monorepo's ``apps/web/tsconfig.json`` is one package's
        config, not the repository's, and claiming it here would mint a
        ``config:tsconfig_json`` node whose id collides with the root's.
        """
        detected = self._detect(("apps/web/tsconfig.json",))
        self.assertNotIn("tsconfig.json", detected)


if __name__ == "__main__":
    unittest.main()
