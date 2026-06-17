"""End-to-end and unit tests for ``wd warm`` orchestration (ADR 0067).

Builds a real git repo with a minimal ``.weld/discover.yaml``, publishes a
graph for an ancestor commit into a local artifact store, then exercises:

* the full warm path (fetch nearest-ancestor + refresh to HEAD picks up local
  edits made after the artifact commit);
* the four fallback paths (no source, unreachable source, hash mismatch,
  non-git) -- each must still yield a graph via full discover;
* sidecar stamping (``git_sha`` recorded for ``wd stale``);
* ``--no-fallback`` (no artifact -> no discover, graph left untouched);
* ``verify_artifact`` rejection rules.

All verification runs against the ``file://`` / local-dir source, so the warm
path is proven without live CI (GitHub Actions billing is unavailable in this
environment).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)

from weld import warm as warm_mod  # noqa: E402
from weld._git import get_git_sha  # noqa: E402

_DISCOVER_YAML = (
    "sources:\n"
    "  - glob: \"src/**/*.py\"\n"
    "    type: symbol\n"
    "    strategy: python_module\n"
    "topology: {}\n"
)

_ENV = {**os.environ, "WELD_TELEMETRY": "off", "GIT_TERMINAL_PROMPT": "0"}


def _git(root: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        env={**_ENV, "LC_ALL": "C"},
    )
    if res.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {res.stderr}")
    return res.stdout.strip()


def _init_repo(root: Path) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(_DISCOVER_YAML, encoding="utf-8")
    (root / "src" / "a.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")


def _publish(root: Path, store: Path, sha: str, *, tag: str | None = "auto") -> None:
    """Discover at the current tree and write the artifact for *sha* into *store*.

    Discovery runs in a **subprocess** (as CI would), so it never populates this
    test process's repo-boundary ``lru_cache`` for *root* -- a warm() call that
    runs in-process afterwards must see files added after this publish, exactly
    as a fresh ``wd warm`` invocation does.
    """
    target_dir = store / sha
    target_dir.mkdir(parents=True, exist_ok=True)
    graph_path = target_dir / "graph.json"
    res = subprocess.run(
        [sys.executable, "-m", "weld", "discover", "--safe",
         "--output", str(graph_path), "--quiet"],
        cwd=str(root), capture_output=True, text=True,
        env={**_ENV, "PYTHONPATH": _repo_root},
    )
    if res.returncode != 0:
        raise AssertionError(f"publish discover failed: {res.stderr}")
    data = graph_path.read_bytes()
    if tag == "auto":
        tag = hashlib.sha256(data).hexdigest()
    if tag is not None:
        (target_dir / "graph.json.sha256").write_text(tag, encoding="utf-8")
    # The publish subprocess wrote a sidecar next to the store graph; the warm
    # fetcher only consumes graph.json + .sha256, so drop the stray sidecar to
    # keep the store to the two published files.
    (target_dir / "graph-meta.json").unlink(missing_ok=True)
    (target_dir / "graph.db").unlink(missing_ok=True)


def _graph_files(root: Path) -> set[str]:
    graph = json.loads((root / ".weld" / "graph.json").read_text(encoding="utf-8"))
    return {
        n.get("props", {}).get("file", "")
        for n in graph.get("nodes", {}).values()
    } - {""}


class WarmFullPathTest(unittest.TestCase):
    def test_fetch_ancestor_then_refresh_picks_up_local_edit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            store = Path(td) / "store"
            root.mkdir()
            _init_repo(root)
            artifact_sha = get_git_sha(root)
            self.assertIsNotNone(artifact_sha)
            _publish(root, store, artifact_sha)  # type: ignore[arg-type]

            # Developer advances one commit past the published artifact.
            (root / "src" / "b.py").write_text(
                "def world():\n    return 2\n", encoding="utf-8"
            )
            _git(root, "add", "-A")
            _git(root, "commit", "-qm", "add b")
            head_sha = get_git_sha(root)

            result = warm_mod.warm(root, source_spec=str(store))

            self.assertEqual(result.outcome, "warmed")
            self.assertEqual(result.artifact_sha, artifact_sha)
            self.assertTrue(result.refreshed)
            # Graph reflects BOTH the artifact (a.py) and the post-artifact
            # local edit (b.py) -- proof the refresh ran on top of the fetch.
            files = _graph_files(root)
            self.assertTrue(any("a.py" in f for f in files), files)
            self.assertTrue(any("b.py" in f for f in files), files)
            # After refresh the sidecar reports HEAD as the basis.
            sidecar = json.loads(
                (root / ".weld" / "graph-meta.json").read_text(encoding="utf-8")
            )
            self.assertEqual(sidecar.get("git_sha"), head_sha)

    def test_head_equals_artifact_is_noop_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            store = Path(td) / "store"
            root.mkdir()
            _init_repo(root)
            sha = get_git_sha(root)
            _publish(root, store, sha)  # type: ignore[arg-type]

            result = warm_mod.warm(root, source_spec=str(store))

            self.assertEqual(result.outcome, "warmed")
            self.assertEqual(result.artifact_sha, sha)
            self.assertTrue(any("a.py" in f for f in _graph_files(root)))


class WarmFallbackTest(unittest.TestCase):
    def test_no_source_falls_back_to_full_discover(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_repo(root)
            env_clear = {k: v for k, v in os.environ.items()
                         if k != warm_mod.ENV_SOURCE}
            old = os.environ.pop(warm_mod.ENV_SOURCE, None)
            try:
                os.environ.update(env_clear)
                result = warm_mod.warm(root, source_spec=None)
            finally:
                if old is not None:
                    os.environ[warm_mod.ENV_SOURCE] = old
            self.assertEqual(result.outcome, "discovered")
            self.assertTrue(result.refreshed)
            self.assertTrue((root / ".weld" / "graph.json").is_file())

    def test_unreachable_source_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_repo(root)
            result = warm_mod.warm(
                root, source_spec=str(Path(td) / "no-such-store")
            )
            self.assertEqual(result.outcome, "discovered")
            self.assertTrue(result.refreshed)
            self.assertTrue((root / ".weld" / "graph.json").is_file())

    def test_hash_mismatch_is_refused_then_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            store = Path(td) / "store"
            root.mkdir()
            _init_repo(root)
            sha = get_git_sha(root)
            # Publish with a deliberately wrong integrity tag.
            _publish(root, store, sha, tag="deadbeef" * 8)  # type: ignore[arg-type]

            result = warm_mod.warm(root, source_spec=str(store))

            self.assertEqual(result.outcome, "discovered")
            self.assertGreaterEqual(result.rejected, 1)
            self.assertTrue(result.refreshed)
            # Fallback still produced a valid graph.
            self.assertTrue(any("a.py" in f for f in _graph_files(root)))

    def test_unverifiable_artifact_without_tag_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            store = Path(td) / "store"
            root.mkdir()
            _init_repo(root)
            sha = get_git_sha(root)
            _publish(root, store, sha, tag=None)  # no .sha256 published

            result = warm_mod.warm(root, source_spec=str(store))

            # No tag => cannot verify => refuse => discover fallback.
            self.assertEqual(result.outcome, "discovered")
            self.assertGreaterEqual(result.rejected, 1)
            self.assertTrue((root / ".weld" / "graph.json").is_file())

    def test_non_git_root_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".weld").mkdir()
            (root / "src").mkdir()
            (root / ".weld" / "discover.yaml").write_text(
                _DISCOVER_YAML, encoding="utf-8"
            )
            (root / "src" / "c.py").write_text("def x():\n    return 1\n",
                                               encoding="utf-8")
            # store is irrelevant; no git => no candidates.
            result = warm_mod.warm(root, source_spec=str(Path(td) / "store"))
            self.assertEqual(result.outcome, "discovered")
            self.assertTrue((root / ".weld" / "graph.json").is_file())


class WarmNoFallbackTest(unittest.TestCase):
    def test_no_fallback_leaves_graph_untouched_on_miss(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_repo(root)
            self.assertFalse((root / ".weld" / "graph.json").is_file())
            result = warm_mod.warm(
                root,
                source_spec=str(Path(td) / "no-store"),
                allow_fallback=False,
            )
            self.assertEqual(result.outcome, "discovered")
            self.assertFalse(result.refreshed)
            self.assertFalse((root / ".weld" / "graph.json").is_file())


class VerifyArtifactTest(unittest.TestCase):
    def test_match(self) -> None:
        data = b'{"nodes": {}}'
        self.assertTrue(
            warm_mod.verify_artifact(data, hashlib.sha256(data).hexdigest())
        )

    def test_match_case_insensitive_expected(self) -> None:
        data = b'{"nodes": {}}'
        digest = hashlib.sha256(data).hexdigest().upper()
        self.assertTrue(warm_mod.verify_artifact(data, digest))

    def test_mismatch(self) -> None:
        self.assertFalse(warm_mod.verify_artifact(b"x", "00" * 32))

    def test_none_tag_is_rejected(self) -> None:
        self.assertFalse(warm_mod.verify_artifact(b"x", None))
        self.assertFalse(warm_mod.verify_artifact(b"x", ""))


class WarmCliTest(unittest.TestCase):
    def test_cli_json_output_on_fallback(self) -> None:
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_repo(root)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = warm_mod.main([
                    str(root), "--source", str(Path(td) / "no-store"), "--json",
                ])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["outcome"], "discovered")
            self.assertIn("refreshed", payload)


if __name__ == "__main__":
    unittest.main()
