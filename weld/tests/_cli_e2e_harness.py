"""Shared plumbing for probes that run the real ``python -m weld`` CLI.

A system-level probe of discovery is always the same six moves: make a temp
tree, write ``.weld/discover.yaml``, ``git init`` it so the ADR 0020
repo-boundary snapshot is non-empty, pin an environment that reads no ambient
config, prove the subprocess imports *this* checkout rather than some other
installed weld, and only then run ``wd`` and read what it wrote.

Two probes wrote that out longhand within a day of each other
(``weld_discover_segment_glob_e2e_test``, ``weld_unclaimed_dialect_e2e_test``),
which is how a third copy becomes inevitable -- the same drift ADR 0112 records
one layer down, where fourteen strategies each kept their own glob resolve. The
harness lives here so a new probe is its fixture tree, its config and its
assertions, and nothing else.

Every classmethod is on a mixin rather than a base ``TestCase`` so unittest
does not collect it standalone, matching ``_exclude_form_harness`` beside it.
:meth:`CliRepoHarness.setup_cli_repo` is called from ``setUpClass``: the tree
is built once and its ``wd discover`` run once, because the interesting
assertions all read the *same* run's graph, inventory and staleness verdict.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

__all__ = [
    "CLI_TIMEOUT_SECONDS",
    "CliRepoHarness",
    "cli_env",
    "weld_import_root",
]

#: Every ``wd`` invocation is bounded. Generous: the assertion is never about
#: how long a command took, only that it terminated (test-hygiene "wallclock").
CLI_TIMEOUT_SECONDS = 180


def weld_import_root() -> str:
    """Directory to put on ``PYTHONPATH`` so ``-m weld`` finds this checkout.

    Under Bazel that is the runfiles tree; from a plain source checkout it is
    the repo root. Both are ``weld/tests/<this file>`` minus three components
    -- the resolved form is tried second because a runfiles entry is a symlink
    into the source tree and only one of the two spellings has ``weld/`` under
    it in every layout.
    """
    here = Path(__file__).absolute()
    for candidate in (here.parents[2], here.resolve().parents[2]):
        if (candidate / "weld" / "__main__.py").is_file():
            return str(candidate)
    raise RuntimeError(  # pragma: no cover - a broken runfiles tree
        f"cannot locate weld/__main__.py above {here}"
    )


def cli_env(home: Path) -> dict[str, str]:
    """The pinned environment every subprocess here runs under.

    ``HOME`` is redirected into the tempdir so no ambient git or weld config
    is read, and ``WELD_AUTO_REFRESH=0`` so ADR 0051's auto-refresh-on-read
    can never rewrite the graph a probe is about to assert on -- which matters
    whenever the defect under test *is* a stale verdict, since an implicit
    refresh would paper over it.
    """
    return {
        "PATH": "/usr/bin:/usr/local/bin:/bin",
        "HOME": str(home),
        "PYTHONPATH": weld_import_root(),
        "PYTHONPYCACHEPREFIX": str(home / "pycache"),
        "PYTHONHASHSEED": "0",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "WELD_TELEMETRY": "off",
        "WELD_AUTO_REFRESH": "0",
        "WELD_SOURCE_CHECKOUT_NOTICE": "off",
    }


class CliRepoHarness:
    """A class-scoped temp git repo plus the ``wd`` runner that drives it.

    Mixed in *before* ``unittest.TestCase``, so every name here shadows the
    ``TestCase`` attribute of the same name. That is why the subprocess runner
    is ``run_argv`` and not the obvious ``run``: ``TestCase.run(result)`` is
    what the test runner itself calls, and shadowing it hands the runner's
    ``TestResult`` to ``subprocess.run`` as an argv list -- a failure whose
    traceback names ``subprocess`` and never mentions the collision.
    """

    #: Set by :meth:`setup_cli_repo`.
    root: Path
    home: Path
    env: dict[str, str]

    @classmethod
    def setup_cli_repo(cls, tree: dict[str, str], config: str | None) -> None:
        """Materialise *tree* and *config* into a fresh git repo under a tempdir.

        Leaves ``cls.root`` and ``cls.env`` ready for :meth:`wd`. Registers its
        own ``addClassCleanup``, so the caller's ``setUpClass`` owns nothing.

        ``config=None`` leaves ``.weld/discover.yaml`` unwritten, for a probe
        whose subject is the config ``wd init`` *generates* rather than one it
        hand-wires: ``wd init`` refuses to overwrite an existing config without
        ``--force``, so an empty string would not have left room for it. Such a
        probe calls ``cls.wd("init")`` itself, after this returns.
        """
        tmp_ctx = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp_ctx.cleanup)  # type: ignore[attr-defined]
        tmp = Path(tmp_ctx.name)
        cls.home = tmp / "home"
        cls.home.mkdir(parents=True, exist_ok=True)
        cls.root = tmp / "repo"
        cls.env = cli_env(cls.home)
        for rel, body in tree.items():
            path = cls.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        (cls.root / ".weld").mkdir(parents=True, exist_ok=True)
        if config is not None:
            (cls.root / ".weld" / "discover.yaml").write_text(
                config, encoding="utf-8"
            )
        cls._git_init()
        cls.assert_cli_is_this_checkout()

    @classmethod
    def _git_init(cls) -> None:
        """A real git repo, because the boundary snapshot is ``git ls-files``.

        The ADR 0101 coverage probe returns empty for a non-git root, so
        without this the whole staleness half of any discovery defect is
        invisible. Identity and signing are set locally so the run does not
        depend on ambient git config, which ``HOME`` has already hidden.
        """
        for cmd in (
            ["init", "--quiet"],
            ["config", "user.email", "test@test.com"],
            ["config", "user.name", "Test"],
            ["config", "commit.gpgsign", "false"],
            ["add", "-A"],
            ["commit", "-m", "initial", "--quiet"],
        ):
            cls.run_argv(["git", *cmd])

    @classmethod
    def run_argv(cls, argv: list[str]) -> subprocess.CompletedProcess:
        """Run *argv* in the fixture repo, raising on a non-zero exit."""
        proc = subprocess.run(
            argv, cwd=str(cls.root), env=cls.env, capture_output=True,
            text=True, input="", timeout=CLI_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"{' '.join(argv)} failed (rc={proc.returncode}):\n"
                f"{proc.stdout}\n{proc.stderr}"
            )
        return proc

    @classmethod
    def wd(cls, *args: str) -> subprocess.CompletedProcess:
        """``python -m weld <args>`` in the fixture repo."""
        return cls.run_argv([sys.executable, "-m", "weld", *args])

    @classmethod
    def assert_cli_is_this_checkout(cls) -> None:
        """The subprocess must import weld from the tree under test.

        A ``python -m weld`` that resolves to some *other* installed weld runs
        green and proves nothing about this branch.
        """
        proc = subprocess.run(
            [sys.executable, "-c", "import weld; print(weld.__file__)"],
            cwd=str(cls.root), env=cls.env, capture_output=True, text=True,
            timeout=CLI_TIMEOUT_SECONDS,
        )
        expected = (Path(weld_import_root()) / "weld").resolve()
        loaded = Path(proc.stdout.strip() or "/nonexistent").parent.resolve()
        if proc.returncode != 0 or loaded != expected:
            raise AssertionError(
                f"the CLI subprocess imports weld from {loaded}, not the tree "
                f"under test ({expected}); rc={proc.returncode}\n{proc.stderr}"
            )

    @classmethod
    def read_json(cls, rel: str) -> dict:
        """Parse a JSON artifact ``wd`` wrote, failing loudly when absent."""
        path = cls.root / rel
        if not path.is_file():
            raise AssertionError(f"{rel} was never written by `wd discover`")
        return json.loads(path.read_text(encoding="utf-8"))
