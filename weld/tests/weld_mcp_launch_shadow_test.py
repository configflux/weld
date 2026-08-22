"""Nothing in the launch directory may run when the MCP server starts.

The companion ``weld_mcp_launch_path_test`` pins the removal logic in
process. These tests are the ones that can prove it happens *early enough*:
the guard has to beat ``weld/mcp_server.py``'s own imports, and no in-process
assertion can observe that ordering. So they launch the real entry point --
``python -m weld.mcp_server`` -- from a directory carrying shadow modules and
assert none of them ran.

The shadows are *stealth* shims: each records that it ran and then hands over
to the real module, so a launch that falls for one still completes. That
models the attack (be executed, stay invisible) and keeps the assertions
about execution rather than about a crash. Every test here is paired with a
negative control that shows the fixture is still effective against an
unguarded interpreter, because a shim that quietly stopped working would
otherwise make all of them pass for the wrong reason.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Recorded, then handed over to the real module so the launch continues. The
# hand-over re-runs the import with this file's own directory excluded, which
# is exactly what the guard under test is supposed to have done already.
_STEALTH_SHIM = '''\
import os as _os, sys as _sys
open(_os.path.join(_os.environ["CANARY_DIR"], "{mod}.hit"), "a").close()
import importlib.machinery as _m, importlib.util as _u
_here = _os.path.dirname(_os.path.abspath(__file__))
_rest = [p for p in _sys.path if p and _os.path.abspath(p) != _here]
_spec = _m.PathFinder.find_spec(__name__, _rest)
if _spec is not None:
    _real = _u.module_from_spec(_spec)
    _sys.modules[__name__] = _real
    _spec.loader.exec_module(_real)
'''

# `dataclasses` is imported by weld/mcp_server.py itself and `json` by the
# tree it pulls in, so both resolve long before the optional SDK probe --
# they are what proves the guard beats weld's *own* imports, not just the
# SDK import.
#
# `__future__` is earlier than either and the sharpest of the three: a
# `from __future__ import annotations` statement is a real import at runtime,
# and a future statement is *required* to be the first statement in its
# module -- so it necessarily runs above a guard that is otherwise correctly
# placed. weld/mcp_server.py therefore carries no future statement, and this
# entry is what says so.
#
# None of these is ever part of the floor `-m` imposes -- the sibling
# `weld_cli_launch_shadow_test` measures that floor and asserts, in its
# `_NEVER_FLOOR`, that all three stay out of it -- which is why the
# assertions below can demand exactly zero rather than subtracting a
# measured floor of their own. If an interpreter ever did put one of them in
# the bootstrap, these tests fail rather than pass vacuously, which is the
# direction to fail in.
_SHADOWED_MODULES = ("__future__", "dataclasses", "json")

#: The shadow the original report was filed about, built as a package rather
#: than a module to match it. Separate from the tuple above because the
#: fixture builds it differently, but it belongs to the same shadow set.
_SHADOWED_PACKAGE = "mcp"


class LaunchDirectoryShadowSubprocessTest(unittest.TestCase):
    """Launch the real entry point from a directory full of shadows."""

    def _shadow_repo(self, root: Path) -> tuple[Path, Path]:
        """Build an "untrusted repository" and the directory it reports into."""
        repo = root / "untrusted-repo"
        canary = root / "canary"
        repo.mkdir()
        canary.mkdir()
        for mod in _SHADOWED_MODULES:
            (repo / f"{mod}.py").write_text(
                _STEALTH_SHIM.format(mod=mod), encoding="utf-8"
            )
        # A package rather than a module, matching the original report: an
        # `mcp/` directory next to the code being analyzed.
        sdk = repo / _SHADOWED_PACKAGE
        sdk.mkdir()
        (sdk / "__init__.py").write_text(
            "import os\n"
            "open(os.path.join(os.environ['CANARY_DIR'], "
            f"'{_SHADOWED_PACKAGE}.hit'), 'a').close()\n",
            encoding="utf-8",
        )
        return repo, canary

    def _env(self, canary: Path) -> dict:
        env = os.environ.copy()
        env["CANARY_DIR"] = str(canary)
        # The child must import weld from this checkout, and must NOT be
        # handed the interpreter flag that would make the guard redundant --
        # inheriting it would let this test pass with the fix reverted.
        repo_root = str(Path(__file__).resolve().parent.parent.parent)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = repo_root + (os.pathsep + existing if existing else "")
        env.pop("PYTHONSAFEPATH", None)
        return env

    def _executed(self, canary: Path) -> set[str]:
        return {hit.stem for hit in canary.glob("*.hit")}

    def test_shadow_modules_fire_under_a_plain_interpreter(self) -> None:
        # Negative control. Without it, every assertion below would also pass
        # against shims that had quietly stopped working. Both the import
        # list and the expectation are derived from the shadow set, so a
        # shadow added to the fixture cannot go unproven here.
        shadows = (*_SHADOWED_MODULES, _SHADOWED_PACKAGE)
        with tempfile.TemporaryDirectory() as tmp:
            repo, canary = self._shadow_repo(Path(tmp))

            proc = subprocess.run(
                [sys.executable, "-c", "import " + ", ".join(shadows)],
                cwd=str(repo),
                env=self._env(canary),
                capture_output=True,
                timeout=60,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(
                self._executed(canary),
                set(shadows),
                "shadow shims did not execute; the tests below would be vacuous",
            )

    def test_help_executes_nothing_from_the_launch_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, canary = self._shadow_repo(Path(tmp))

            proc = subprocess.run(
                [sys.executable, "-m", "weld.mcp_server", "--help"],
                cwd=str(repo),
                env=self._env(canary),
                capture_output=True,
                text=True,
                timeout=60,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Usage: python -m weld.mcp_server", proc.stdout)
            self.assertEqual(
                self._executed(canary),
                set(),
                "the launch directory answered an import made by the server",
            )

    def test_serving_executes_nothing_from_the_launch_directory(self) -> None:
        # The serve path is the one that reaches the optional SDK probe, so
        # it is the only one that can prove the reported `mcp/` shadow is
        # gone. Closed stdin ends the session immediately if a usable SDK is
        # present; without one the server exits 2 after the probe. Both
        # outcomes reach the import under test.
        with tempfile.TemporaryDirectory() as tmp:
            repo, canary = self._shadow_repo(Path(tmp))

            proc = subprocess.run(
                [sys.executable, "-m", "weld.mcp_server"],
                cwd=str(repo),
                input=b"",
                env=self._env(canary),
                capture_output=True,
                timeout=60,
            )

            self.assertIn(
                proc.returncode,
                (0, 2),
                f"unexpected exit {proc.returncode}; stderr={proc.stderr!r}",
            )
            self.assertEqual(
                self._executed(canary),
                set(),
                "the launch directory answered an import made by the server",
            )

    def test_repo_local_mcp_package_no_longer_shadows_the_sdk(self) -> None:
        # The original report, stated as behavior: the guard must not merely
        # skip the shadow, it must leave the SDK state reported as whatever
        # the *installed* environment actually is. A shadowed `mcp` package
        # has no `mcp.server` submodule, so falling for it is visible as that
        # import failing by name.
        with tempfile.TemporaryDirectory() as tmp:
            repo, canary = self._shadow_repo(Path(tmp))

            proc = subprocess.run(
                [sys.executable, "-m", "weld.mcp_server"],
                cwd=str(repo),
                input="",
                env=self._env(canary),
                capture_output=True,
                text=True,
                timeout=60,
            )

            self.assertNotIn(_SHADOWED_PACKAGE, self._executed(canary))
            self.assertNotIn(
                "No module named 'mcp.server'",
                proc.stderr,
                "the repo-local mcp package was imported in place of the SDK",
            )


class ForgedDistInfoTest(unittest.TestCase):
    """A repository must not get to choose the version weld prints.

    The SDK-state hint reports ``importlib.metadata.version("mcp")``, and
    distribution metadata is discovered along ``sys.path`` -- so a crafted
    ``*.dist-info`` in the launch directory could put an attacker-chosen
    string into a message the user is being asked to act on. Same
    precondition as the shadowed SDK, so the same removal closes it.
    """

    _FORGED = "99.9.9-forged"
    # Substrings unique to the two branches that print a version. Their
    # absence means the SDK was usable and the server started instead, which
    # is a different code path with no version string in it.
    _HINT_MARKERS = ("does not provide", "not installed")

    def _launch_dir(self, root: Path) -> Path:
        launch = root / "untrusted-repo"
        dist = launch / "mcp-99.9.9.dist-info"
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: mcp\nVersion: {self._FORGED}\n",
            encoding="utf-8",
        )
        (dist / "RECORD").write_text("", encoding="utf-8")
        return launch

    def _env(self) -> dict:
        env = os.environ.copy()
        repo_root = str(Path(__file__).resolve().parent.parent.parent)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = repo_root + (os.pathsep + existing if existing else "")
        env.pop("PYTHONSAFEPATH", None)
        return env

    def test_forged_metadata_does_not_reach_the_sdk_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            launch = self._launch_dir(Path(tmp))
            env = self._env()

            # Control: the forgery is effective against an interpreter that
            # still has the launch directory on its path. Without this, the
            # assertion below would also pass against a broken fixture.
            control = subprocess.run(
                [
                    sys.executable, "-c",
                    "from importlib.metadata import version;"
                    "print(version('mcp'))",
                ],
                cwd=str(launch), env=env, capture_output=True, text=True,
                timeout=60,
            )
            if control.returncode != 0:
                self.skipTest("no mcp distribution metadata to shadow here")
            self.assertIn(
                self._FORGED,
                control.stdout,
                "fixture did not shadow the metadata; the test would be vacuous",
            )

            proc = subprocess.run(
                [sys.executable, "-m", "weld.mcp_server"],
                cwd=str(launch), input="", env=env, capture_output=True,
                text=True, timeout=60,
            )

            if not any(m in proc.stderr for m in self._HINT_MARKERS):
                self.skipTest(
                    "usable SDK present; the server started instead of "
                    "reporting a version"
                )
            self.assertNotIn(
                self._FORGED,
                proc.stderr,
                "a repository chose the SDK version weld reported to the user",
            )


if __name__ == "__main__":
    unittest.main()
