"""Regression: ``wd discover`` must keep ``.weld/file-index.json`` in sync.

Background
----------
The file index is the substrate that backs ``wd find``. Historically the
index was only written by the standalone ``wd build-index`` verb, so a
fresh checkout that ran ``wd discover`` (or the auto-refresh path) ended
up with a populated graph but no file index. ``wd find`` then returned
empty results for symbols that very clearly existed on disk and in the
graph -- the canonical dogfood gap that motivated this regression.

The fix wires ``build_file_index`` / ``save_file_index`` into all three
exit paths of ``_discover_single_repo`` (full discovery, incremental with
changes, incremental no-changes). This test pins the contract.

Fixtures
--------
The test uses neutral package names (``pkg_alpha``, ``pkg_beta``) and
trivial Python content so it has no entanglement with real repository
modules.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.discover import _discover_single_repo  # noqa: E402
from weld.file_index import find_files, load_file_index  # noqa: E402


def _build_fixture(root: Path) -> None:
    """Two-module fixture: pkg_alpha and pkg_beta, one file each."""
    src = root / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "pkg_alpha.py").write_text(
        "def alpha_helper():\n    return 1\n",
        encoding="utf-8",
    )
    (src / "pkg_beta.py").write_text(
        "def beta_helper():\n    return 2\n",
        encoding="utf-8",
    )

    (root / ".weld").mkdir()
    (root / ".weld" / "discover.yaml").write_text(
        "topology:\n"
        "  nodes:\n"
        "    - id: pkg:src\n"
        "      type: package\n"
        "      label: src\n"
        "sources:\n"
        "  - strategy: python_module\n"
        "    glob: src/**/*.py\n"
        "    type: file\n"
        "    package: pkg:src\n",
        encoding="utf-8",
    )


class DiscoverWritesFileIndexTest(unittest.TestCase):
    """Discovery must produce a non-empty ``file-index.json`` covering the tree."""

    def test_full_discovery_writes_file_index(self) -> None:
        with tempfile.TemporaryDirectory(prefix="discover-fidx-full-") as td:
            root = Path(td)
            _build_fixture(root)

            _discover_single_repo(root, incremental=False)

            idx_path = root / ".weld" / "file-index.json"
            self.assertTrue(
                idx_path.is_file(),
                "wd discover must write .weld/file-index.json",
            )

            envelope = json.loads(idx_path.read_text(encoding="utf-8"))
            files = envelope.get("files", envelope)
            self.assertIn("src/pkg_alpha.py", files)
            self.assertIn("src/pkg_beta.py", files)

    def test_find_after_discovery_returns_fixture_files(self) -> None:
        """``find_files`` against the freshly written index must surface fixtures."""
        with tempfile.TemporaryDirectory(prefix="discover-fidx-find-") as td:
            root = Path(td)
            _build_fixture(root)

            _discover_single_repo(root, incremental=False)
            index = load_file_index(root)
            result = find_files(index, "pkg_alpha")

            paths = [entry["path"] for entry in result["files"]]
            self.assertIn("src/pkg_alpha.py", paths)

    def test_incremental_no_changes_path_recreates_missing_index(self) -> None:
        """A wiped file-index must be re-created on the no-changes fast path.

        The motivating dogfood gap: a stale state file pointed at an
        up-to-date graph, the no-changes branch short-circuited
        discovery, and the file index never reappeared even though the
        user explicitly re-ran ``wd discover``.
        """
        with tempfile.TemporaryDirectory(prefix="discover-fidx-nochg-") as td:
            root = Path(td)
            _build_fixture(root)

            # Seed full state, then wipe just the file index.
            _discover_single_repo(root, incremental=False)
            idx_path = root / ".weld" / "file-index.json"
            self.assertTrue(idx_path.is_file())
            idx_path.unlink()
            self.assertFalse(idx_path.is_file())

            # Second run with no source changes must hit the
            # incremental fast path and still reconstruct the index.
            _discover_single_repo(root, incremental=True)

            self.assertTrue(
                idx_path.is_file(),
                "no-changes incremental path must rebuild the missing index",
            )
            envelope = json.loads(idx_path.read_text(encoding="utf-8"))
            files = envelope.get("files", envelope)
            self.assertIn("src/pkg_alpha.py", files)
            self.assertIn("src/pkg_beta.py", files)

    def test_incremental_with_changes_refreshes_file_index(self) -> None:
        """Adding a file then re-running discovery must update the index."""
        with tempfile.TemporaryDirectory(prefix="discover-fidx-add-") as td:
            root = Path(td)
            _build_fixture(root)

            _discover_single_repo(root, incremental=False)

            # Add a new module -- the incremental path with changes
            # must include it in the regenerated file index.
            new_module = root / "src" / "pkg_gamma.py"
            new_module.write_text(
                "def gamma_helper():\n    return 3\n",
                encoding="utf-8",
            )

            _discover_single_repo(root, incremental=True)

            envelope = json.loads(
                (root / ".weld" / "file-index.json").read_text(encoding="utf-8")
            )
            files = envelope.get("files", envelope)
            self.assertIn("src/pkg_gamma.py", files)


if __name__ == "__main__":
    unittest.main()
