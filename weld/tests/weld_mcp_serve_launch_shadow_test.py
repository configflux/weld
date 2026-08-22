"""``wd mcp serve`` must execute nothing at all from its launch directory.

The sibling ``weld_mcp_launch_shadow_test`` holds ``python -m
weld.mcp_server`` to the best that launch form allows: no *weld* import may
be answered by the launch directory. It cannot ask for zero, because ``-m``
prepends the working directory to ``sys.path`` before CPython's ``runpy``
bootstrap runs, and that bootstrap imports a handful of modules before the
target module's first line. Every ``-m`` target pays that floor.

A console script has no launch-directory entry at all -- ``sys.path[0]`` is
the script's own directory -- so ``wd mcp serve`` can be held to zero, and
this file is what holds it there. The shadow set is deliberately wider than
the sibling's: it carries the floor members too, so a regression that
reintroduced a ``-m``-shaped launch would surface here as floor hits rather
than pass unnoticed.

Two controls keep the zero honest, because "nothing executed" is also what a
broken fixture looks like:

* the negative control imports the whole shadow set under a plain
  interpreter and requires every shim to fire;
* the floor control launches an empty package with ``-m`` from the same
  directory and requires a *non-empty* result -- that is the residual this
  launch form exists to close, measured rather than assumed.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Recorded, then handed over to the real module so the launch continues. The
# hand-over re-runs the import with this file's own directory excluded, which
# is what a launch form without a launch-directory entry gets for free.
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

#: Modules CPython's ``runpy`` bootstrap resolves before a ``-m`` target's
#: first line, on the interpreter this was written against. Membership and
#: size both move with the version, which is why the floor control *measures*
#: the set rather than comparing against this tuple -- it is here only to make
#: the shadow set wide enough that a ``-m``-shaped regression cannot hide.
_RUNPY_FLOOR_MODULES = (
    "collections",
    "functools",
    "keyword",
    "operator",
    "reprlib",
    "threading",
    "types",
    "warnings",
)

#: Imports the server itself reaches, above any floor. ``__future__`` is the
#: sharpest: a future statement is required to be the first statement in its
#: module and is a real runtime import, so it lands above any guard.
_SERVER_MODULES = ("__future__", "dataclasses", "json")

_SHADOWED_MODULES = tuple(sorted(_RUNPY_FLOOR_MODULES + _SERVER_MODULES))

#: The shadow the original report was filed about, built as a package rather
#: than a module to match it: an ``mcp/`` directory beside the code served.
_SHADOWED_PACKAGE = "mcp"

#: Entered the way a console script is: a plain script file, run from a
#: directory of its own, calling the same ``weld.cli:main`` that the ``wd``
#: entry point in ``weld/pyproject.toml`` binds.
_CONSOLE_SCRIPT = "import sys\nfrom weld.cli import main\nsys.exit(main())\n"


class ServeLaunchDirectoryShadowTest(unittest.TestCase):
    """Launch the console-script form from a directory full of shadows."""

    def _probe(
        self,
        argv: list[str],
        *,
        script: str | None = None,
        expected_returncodes: tuple[int, ...] = (0,),
    ) -> tuple[set[str], "subprocess.CompletedProcess[str]"]:
        """Run one launch in a fresh shadow directory.

        Returns what executed and the completed process, so a caller can also
        assert on *where* the launch got to -- a launch that never reached the
        server executes nothing either, and would otherwise pass for free.
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
            sdk = repo / _SHADOWED_PACKAGE
            sdk.mkdir()
            (sdk / "__init__.py").write_text(
                "import os\n"
                "open(os.path.join(os.environ['CANARY_DIR'], "
                f"'{_SHADOWED_PACKAGE}.hit'), 'a').close()\n",
                encoding="utf-8",
            )
            # A `-m` target that imports nothing, used to measure the floor.
            # It lives outside the shadow directory and is reached over
            # PYTHONPATH, so the floor probe finds it regardless.
            empty = root / "floor" / "emptypkg"
            empty.mkdir(parents=True)
            (empty / "__init__.py").write_text("", encoding="utf-8")
            (empty / "__main__.py").write_text("", encoding="utf-8")

            env = os.environ.copy()
            env["CANARY_DIR"] = str(canary)
            # The child must import weld from this checkout, and must NOT be
            # handed the interpreter flag that would make the result
            # meaningless -- inheriting PYTHONSAFEPATH would produce a clean
            # launch no matter what the entry point did. Inherited entries are
            # made absolute first: a relative entry means "where the parent
            # was", but the child runs in the shadow directory, so passing one
            # through verbatim would hand the untrusted repository a path
            # entry and fail these tests for the one reason they must never
            # fail for.
            repo_root = str(Path(__file__).resolve().parent.parent.parent)
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
                entry_point = script_dir / "wd_proxy.py"
                entry_point.write_text(script, encoding="utf-8")
                command = [sys.executable, str(entry_point), *argv]
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
            self.assertIn(
                proc.returncode,
                expected_returncodes,
                f"unexpected exit {proc.returncode}: {proc.stderr[-2000:]}",
            )
            return {hit.stem for hit in canary.glob("*.hit")}, proc

    def test_shadow_modules_fire_under_a_plain_interpreter(self) -> None:
        # Negative control. Without it, every assertion below would also pass
        # against shims that had quietly stopped working. Both the import list
        # and the expectation derive from the shadow set, so a shadow added to
        # the fixture cannot go unproven here.
        shadows = (*_SHADOWED_MODULES, _SHADOWED_PACKAGE)
        executed, _proc = self._probe(["-c", "import " + ", ".join(shadows)])

        self.assertEqual(
            executed,
            set(shadows),
            "shadow shims did not execute; the tests below would be vacuous",
        )

    def test_the_runpy_floor_this_launch_form_closes_is_real(self) -> None:
        # The floor control. An empty `-m` target is what a perfectly clean
        # `-m` launch looks like, and it still executes code from the
        # directory it was aimed at. Measured rather than hard-coded, because
        # the membership moves with the interpreter version; all this needs to
        # establish is that the residual is non-empty, so the zero asserted
        # below is a result rather than an inert fixture.
        executed, _proc = self._probe(["-m", "emptypkg"])

        self.assertNotEqual(
            executed,
            set(),
            "no shadow fired under `-m`; the console-script zero below would "
            "prove nothing",
        )

    def test_serve_help_executes_nothing_at_all(self) -> None:
        executed, proc = self._probe(
            ["mcp", "serve", "--help"], script=_CONSOLE_SCRIPT
        )

        # The banner is also the proof that dispatch reached the transport
        # module rather than stopping at the `wd mcp` subcommand table.
        self.assertIn("Usage: wd mcp serve", proc.stdout)
        self.assertEqual(
            executed,
            set(),
            "the launch directory answered an import made by the server",
        )

    def test_serving_executes_nothing_at_all(self) -> None:
        # The serve path is the one that reaches the optional SDK probe, so it
        # is the only one that can prove the reported `mcp/` shadow is gone.
        # Closed stdin ends the session immediately when a usable SDK is
        # present; without one the server exits 2 after the probe. Both
        # outcomes reach the import under test -- and both have to be told
        # apart from an argument error, which also exits 2 and also executes
        # nothing, so it would satisfy the assertion below for free.
        executed, proc = self._probe(
            ["mcp", "serve"],
            script=_CONSOLE_SCRIPT,
            expected_returncodes=(0, 2),
        )

        self.assertTrue(
            proc.returncode == 0 or "the 'mcp' Python SDK" in proc.stderr,
            "launch did not reach the SDK probe, so nothing here is proven: "
            f"rc={proc.returncode} stderr={proc.stderr[-2000:]}",
        )
        self.assertEqual(
            executed,
            set(),
            "the launch directory answered an import made by the server",
        )


if __name__ == "__main__":
    unittest.main()
