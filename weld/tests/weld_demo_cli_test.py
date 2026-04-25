"""CLI tests for the ``wd demo`` command family.

Covers the four documented surfaces:

* ``wd demo list`` (text and ``--json``).
* ``wd demo monorepo --init <dir>`` materializes the monorepo layout
  (single root ``.git/``, ``.weld/discover.yaml``, expected packages).
* ``wd demo polyrepo --init <dir>`` materializes the polyrepo layout
  (one ``.git/`` per child plus a root ``.weld/workspaces.yaml``).
* Failure modes: missing ``--init``, target directory already populated.

Tests run the bundled scripts via :func:`weld.demo.main` so they
exercise the same code path the CLI uses, and they configure a
throwaway git identity in ``HOME`` so the bootstrap scripts succeed in
hermetic environments.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import demo as demo_mod  # noqa: E402


def _seed_git_identity(home: Path) -> dict[str, str]:
    """Return an env dict with HOME pointed at a throwaway git identity."""
    home.mkdir(parents=True, exist_ok=True)
    gitconfig = home / ".gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = Weld Demo Test\n\temail = demo-test@example.com\n",
        encoding="utf-8",
    )
    return {
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
    }


class DemoListTest(unittest.TestCase):
    """``wd demo list`` enumerates available demos."""

    def test_list_text_includes_both_demos(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = demo_mod.main(["list"])
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        self.assertIn("monorepo", output)
        self.assertIn("polyrepo", output)
        self.assertIn("wd demo <name> --init", output)

    def test_list_json_payload(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = demo_mod.main(["list", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertIsInstance(payload, list)
        names = sorted(item["name"] for item in payload)
        self.assertEqual(names, ["monorepo", "polyrepo"])
        for item in payload:
            self.assertIn("script", item)
            self.assertIn("description", item)
            self.assertTrue(item["script"].endswith(".sh"))


class _DemoRunCase(unittest.TestCase):
    """Shared setup: hermetic HOME with a configured git identity."""

    def setUp(self) -> None:
        if shutil.which("bash") is None or shutil.which("git") is None:
            self.skipTest("bash and git are required for demo bootstrap tests")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._tmp_root = Path(self._tmp.name)

        env_overrides = _seed_git_identity(self._tmp_root / "home")
        self._saved_env: dict[str, str | None] = {}
        for key, value in env_overrides.items():
            self._saved_env[key] = os.environ.get(key)
            os.environ[key] = value

    def tearDown(self) -> None:
        for key, prior in self._saved_env.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior

    def _target(self, name: str) -> Path:
        return self._tmp_root / name


class DemoMonorepoTest(_DemoRunCase):
    """``wd demo monorepo --init <dir>`` materializes the monorepo demo."""

    def test_monorepo_layout_created(self) -> None:
        target = self._target("mono")
        rc = demo_mod.main(["monorepo", "--init", str(target)])
        self.assertEqual(rc, 0, "wd demo monorepo should exit 0")

        # Single root git repo, the monorepo invariant.
        self.assertTrue((target / ".git").is_dir())
        self.assertFalse((target / "packages" / "ui" / ".git").exists())

        # Discovery config is written at the root.
        self.assertTrue((target / ".weld" / "discover.yaml").is_file())

        # Seeded packages and services exist with expected source files.
        for rel in (
            "package.json",
            "packages/ui/src/Button.tsx",
            "packages/api/src/client.ts",
            "apps/web/src/App.tsx",
            "libs/shared-types/src/index.ts",
            "services/orders-api/src/server.ts",
        ):
            self.assertTrue(
                (target / rel).is_file(), f"missing {rel}"
            )


class DemoPolyrepoTest(_DemoRunCase):
    """``wd demo polyrepo --init <dir>`` materializes nested git repos."""

    def test_polyrepo_layout_creates_child_git_repos(self) -> None:
        target = self._target("poly")
        rc = demo_mod.main(["polyrepo", "--init", str(target)])
        self.assertEqual(rc, 0, "wd demo polyrepo should exit 0")

        # Root has the workspaces registry; each child has its own .git.
        self.assertTrue((target / ".weld" / "workspaces.yaml").is_file())
        for child in (
            "services/api",
            "services/auth",
            "libs/shared-models",
        ):
            self.assertTrue(
                (target / child / ".git").is_dir(),
                f"missing .git in {child}",
            )
            self.assertTrue(
                (target / child / ".weld" / "discover.yaml").is_file(),
                f"missing discover.yaml in {child}",
            )

        # POST /tokens lives in the auth service -- the cross-repo target.
        tokens = target / "services" / "auth" / "src" / "routers" / "tokens.py"
        self.assertTrue(tokens.is_file())
        self.assertIn("POST /tokens", tokens.read_text(encoding="utf-8"))


class DemoFailureModesTest(_DemoRunCase):
    """Failure modes surface as non-zero exit codes."""

    def test_missing_init_argument_errors(self) -> None:
        # argparse exits with SystemExit(2) when a required argument is missing.
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                demo_mod.main(["monorepo"])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_populated_target_directory_rejected(self) -> None:
        target = self._target("populated")
        target.mkdir()
        (target / "stray.txt").write_text("nope\n", encoding="utf-8")

        # The bootstrap script writes a one-line error to stderr; capture it
        # so the test output stays quiet without losing the actual exit-code
        # assertion.
        with open(os.devnull, "w") as devnull:
            saved_stderr = sys.stderr
            sys.stderr = devnull
            try:
                rc = demo_mod.main(["monorepo", "--init", str(target)])
            finally:
                sys.stderr = saved_stderr
        self.assertNotEqual(rc, 0)
        # File we put there should still be present (script bailed early).
        self.assertTrue((target / "stray.txt").is_file())


class DemoBundledScriptsParityTest(unittest.TestCase):
    """The vendored copies under ``weld/demos/scripts`` must match repo root.

    ``scripts/create-{mono,poly}repo-demo.sh`` and
    ``scripts/_demo_common.sh`` are the canonical user-facing entrypoints.
    The package vendors byte-identical copies under
    ``weld/demos/scripts/`` so ``wd demo`` keeps working when installed
    from a wheel; this test fails loudly if the two copies drift.
    """

    def test_vendored_scripts_byte_match_repo_scripts(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        repo_scripts = repo_root / "scripts"
        if not repo_scripts.is_dir():
            self.skipTest(
                "repo-root scripts/ not present (e.g. wheel-only test layout)"
            )
        vendored = (
            Path(__file__).resolve().parent.parent / "demos" / "scripts"
        )
        for filename in (
            "_demo_common.sh",
            "create-monorepo-demo.sh",
            "create-polyrepo-demo.sh",
        ):
            self.assertEqual(
                (vendored / filename).read_bytes(),
                (repo_scripts / filename).read_bytes(),
                f"vendored {filename} drifted from scripts/{filename}",
            )


if __name__ == "__main__":
    unittest.main()
