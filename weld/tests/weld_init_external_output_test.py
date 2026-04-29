"""Regression test for tracked issue: ``wd init --output`` must not leak into ``<root>/.weld/``.

Before the fix, ``wd init`` always wrote ``workspaces.yaml`` to
``<root>/.weld/workspaces.yaml`` (where ``root`` came from ``args.root`` or
cwd), regardless of where ``--output`` pointed. When test scripts ran
``wd init --output=$TMP/.weld/discover.yaml`` with cwd=project-root, the
implicit ``workspaces.yaml`` write corrupted the source-of-truth ``.weld/``
and flipped subsequent ``wd discover`` invocations into federation mode.

After the fix, ``workspaces.yaml`` lives next to ``discover.yaml`` -- i.e.
in the same ``.weld/`` directory the operator named via ``--output``. The
default ``--output`` (``<root>/.weld/discover.yaml``) preserves prior
behavior; only externalised ``--output`` invocations now stay externalised.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from weld.init import main as init_main  # noqa: E402


def _make_nested_git_repo(parent: Path, name: str) -> Path:
    """Create a directory containing a ``.git/`` so the scanner sees a child."""
    repo = parent / name
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--quiet", str(repo)],
        check=True, capture_output=True,
    )
    return repo


class InitExternalOutputTest(unittest.TestCase):
    def test_external_output_does_not_touch_root_weld(self) -> None:
        """``wd init --output=$TMP_EXT/.weld/discover.yaml`` from cwd=$TMP_ROOT
        with a nested git repo under $TMP_ROOT must not create
        ``$TMP_ROOT/.weld/workspaces.yaml``.

        Pre-fix, the scanner found the nested repo and wrote workspaces.yaml
        into ``$TMP_ROOT/.weld/`` even though ``--output`` named ``$TMP_EXT``.
        """
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            tmp_root = base / "project"
            tmp_ext = base / "external"
            tmp_root.mkdir()
            tmp_ext.mkdir()
            # Make tmp_root itself a git repo plus a nested child so the
            # scanner classifies it as a polyrepo workspace.
            subprocess.run(
                ["git", "init", "--quiet", str(tmp_root)],
                check=True, capture_output=True,
            )
            _make_nested_git_repo(tmp_root, "child-a")

            # Run ``wd init --output=external/.weld/discover.yaml`` with
            # ``args.root`` pointing at tmp_root explicitly. Pre-fix this
            # always wrote ``tmp_root/.weld/workspaces.yaml``.
            ext_discover = tmp_ext / ".weld" / "discover.yaml"
            init_main(
                [
                    str(tmp_root),
                    "--output", str(ext_discover),
                    "--max-depth", "4",
                ],
            )

            # External target received discover.yaml AND workspaces.yaml.
            self.assertTrue(ext_discover.is_file())
            ext_workspaces = tmp_ext / ".weld" / "workspaces.yaml"
            self.assertTrue(
                ext_workspaces.is_file(),
                "external --output should still receive workspaces.yaml when "
                "scanner detects nested children",
            )

            # Project root .weld/ must not have been written.
            root_workspaces = tmp_root / ".weld" / "workspaces.yaml"
            self.assertFalse(
                root_workspaces.exists(),
                "wd init with external --output must not create "
                "<root>/.weld/workspaces.yaml (tracked issue)",
            )
            root_discover = tmp_root / ".weld" / "discover.yaml"
            self.assertFalse(
                root_discover.exists(),
                "wd init with external --output must not create "
                "<root>/.weld/discover.yaml (tracked issue)",
            )

    def test_default_output_still_writes_to_root_weld(self) -> None:
        """When ``--output`` is omitted, behavior is unchanged: both
        ``discover.yaml`` and ``workspaces.yaml`` land in ``<root>/.weld/``.
        """
        with tempfile.TemporaryDirectory() as td:
            tmp_root = Path(td)
            subprocess.run(
                ["git", "init", "--quiet", str(tmp_root)],
                check=True, capture_output=True,
            )
            _make_nested_git_repo(tmp_root, "child-a")

            init_main([str(tmp_root), "--max-depth", "4"])

            self.assertTrue((tmp_root / ".weld" / "discover.yaml").is_file())
            self.assertTrue((tmp_root / ".weld" / "workspaces.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
