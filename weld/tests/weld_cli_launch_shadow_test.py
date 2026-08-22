"""``python -m weld`` must not run code from the directory it is aimed at.

README's trust model says of safe mode: "scan an untrusted repository without
executing any code from it". That holds for the ``wd`` console script, whose
``sys.path[0]`` is the script's own directory. It did not hold for the
documented raw-source path ``python -m weld``, which puts the working
directory -- the repository being scanned -- ahead of the standard library.

The companion ``weld_cli_launch_path_test`` pins the removal in process.
These tests are the ones that can prove it happens *early enough*: the guard
has to beat the CLI's own imports, and no in-process assertion can observe
that ordering. So they launch the real entry point from a directory carrying
shadow modules and assert none of them ran.

The shadows are *stealth* shims: each records that it ran and then hands over
to the real module, so a launch that falls for one still completes. That
models the attack (be executed, stay invisible) and keeps the assertions
about execution rather than about a crash.

``-m`` imposes a floor that no target can get under: CPython's ``runpy``
bootstrap imports a handful of modules before the target module's first line.
The floor is interpreter-dependent, so it is *measured* here rather than
hard-coded -- an empty package launched the same way, from the same
directory, is what a perfectly clean ``-m`` target looks like. Asserting
against a measured floor is only meaningful if the floor is small, so
:meth:`ShadowLaunchTest._floor` asserts that too; otherwise a floor that had
swallowed the whole shadow set would make every test below pass for free.
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

# Startup imports of the CLI and the tree it pulls in. `__future__` is the
# sharp one: a `from __future__ import annotations` statement is a real
# runtime import, so it is reachable by the *first* weld module a guarded
# entry point imports -- earlier than anything else on this list, and early
# enough to slip under a guard that is otherwise correctly placed.
_SHADOWED_MODULES = (
    "__future__",
    "argparse",
    "dataclasses",
    "json",
    "pathlib",
    "socket",
    "sqlite3",
    "subprocess",
)

# Modules that must never be dismissed as "the interpreter did it". If the
# measured floor ever contains one of these, the floor stopped being a floor
# and the subset assertions below would be vacuous.
_NEVER_FLOOR = ("__future__", "dataclasses", "json", "sqlite3")


class ShadowLaunchTest(unittest.TestCase):
    """Launch the real entry point from a directory full of shadows."""

    def _probe(self, argv: list[str], *, script: str | None = None) -> set[str]:
        """Run one launch in a fresh shadow directory; return what executed.

        *script* runs the given source as a plain script from a directory of
        its own, which is how a console script is entered.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "untrusted-repo"
            canary = root / "canary"
            repo.mkdir()
            canary.mkdir()
            for mod in _SHADOWED_MODULES:
                (repo / f"{mod}.py").write_text(
                    _STEALTH_SHIM.format(mod=mod), encoding="utf-8"
                )
            # A `-m` target that imports nothing, used to measure the floor.
            # It lives outside the shadow directory and is reached over
            # PYTHONPATH, so it stays importable after the entry is dropped.
            empty = root / "floor" / "emptypkg"
            empty.mkdir(parents=True)
            (empty / "__init__.py").write_text("", encoding="utf-8")
            (empty / "__main__.py").write_text("", encoding="utf-8")

            env = os.environ.copy()
            env["CANARY_DIR"] = str(canary)
            # The child must import weld from this checkout, and must NOT be
            # handed the interpreter flag that would make the guard redundant
            # -- inheriting it would let these tests pass with the fix
            # reverted.
            repo_root = str(Path(__file__).resolve().parent.parent.parent)
            # Inherited entries are made absolute first. A relative entry --
            # `.` is the common one -- means "where the parent was", but the
            # child runs in the shadow directory, so passing it through
            # verbatim would hand the untrusted repository a path entry and
            # fail these tests for the one reason they must never fail for.
            inherited = [
                os.path.abspath(entry)
                for entry in env.get("PYTHONPATH", "").split(os.pathsep)
                if entry
            ]
            env["PYTHONPATH"] = os.pathsep.join(
                [repo_root, str(root / "floor"), *inherited]
            )
            env.pop("PYTHONSAFEPATH", None)

            if script is not None:
                script_dir = root / "bin"
                script_dir.mkdir()
                entry = script_dir / "wd_proxy.py"
                entry.write_text(script, encoding="utf-8")
                command = [sys.executable, str(entry), *argv]
            else:
                command = [sys.executable, *argv]

            proc = subprocess.run(
                command,
                cwd=str(repo),
                env=env,
                input="",
                capture_output=True,
                text=True,
                timeout=300,
            )
            self.assertEqual(
                proc.returncode, 0, f"launch failed: {proc.stderr[-2000:]}"
            )
            return {hit.stem for hit in canary.glob("*.hit")}

    def _floor(self) -> set[str]:
        """What an empty ``-m`` target executes: the floor ``-m`` imposes."""
        floor = self._probe(["-m", "emptypkg"])
        overlap = sorted(set(_NEVER_FLOOR) & floor)
        self.assertEqual(
            overlap,
            [],
            f"{overlap} came from the interpreter, not from weld; the "
            "assertions in this file would no longer mean anything",
        )
        return floor

    def test_shadow_modules_fire_under_a_plain_interpreter(self) -> None:
        # Negative control. Without it, every assertion below would also pass
        # against shims that had quietly stopped working.
        imports = ", ".join(_SHADOWED_MODULES)
        executed = self._probe(["-c", f"import {imports}"])

        self.assertEqual(
            executed,
            set(_SHADOWED_MODULES),
            "shadow shims did not execute; the tests below would be vacuous",
        )

    def test_version_executes_nothing_beyond_the_runpy_floor(self) -> None:
        floor = self._floor()

        executed = self._probe(["-m", "weld", "--version"])

        self.assertEqual(
            executed - floor,
            set(),
            "the launch directory answered an import made by the CLI",
        )

    def test_safe_discovery_executes_nothing_beyond_the_runpy_floor(self) -> None:
        # The documented claim, stated as behavior: `--safe` is advertised as
        # scanning an untrusted repository without executing code from it, and
        # this is that repository.
        floor = self._floor()

        executed = self._probe(["-m", "weld", "discover", "--safe"])

        self.assertEqual(
            executed - floor,
            set(),
            "a repository under --safe discovery executed its own code",
        )

    def test_console_script_form_executes_nothing_at_all(self) -> None:
        # `wd` is the recommended surface precisely because it has no
        # residual: a script's sys.path[0] is the script's own directory, so
        # the launch directory is never on the path to begin with. This is
        # what the CLI entry is being held to, minus the floor `-m` adds.
        executed = self._probe(
            ["--version"],
            script="import sys\nfrom weld.cli import main\nsys.exit(main())\n",
        )

        self.assertEqual(executed, set())


if __name__ == "__main__":
    unittest.main()
