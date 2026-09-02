"""Drive the field-eval fixture through the **real** ``wd`` CLI.

The v0.23.1 corpus ran in-process: it hand-built a ``ResolverContext``,
hard-coded ``cross_repo_strategies: []``, and never ran ``wd init`` /
``wd discover`` / ``wd stale`` / ``wd find`` / ``wd doctor``. Nine defects
walked past it, and every one of them lives on a route that corpus never
entered -- ``workspaces.yaml`` parsing, ``merge_cross_repo_edges``, the config
generators, the CLI renderers. So this harness shells the CLI as the evaluator
did: ``python -m weld`` in a subprocess, one workspace on disk, the graphs the
product itself writes.

The bootstrap is a port of the bundle's ``fixture/bootstrap-fixture.sh``:
``wd init`` + ``wd discover --safe`` in each child, then a federated
``wd discover`` at the root. It runs once per test module (``setUpModule``);
the probes on top of it are what each test method owns.

Environment is pinned rather than inherited -- fixed ``PYTHONHASHSEED``/``TZ``/
locale, ``HOME`` redirected into the tempdir so no ambient git or weld config
is read, telemetry off, and ``WELD_AUTO_REFRESH=0`` so ADR 0051's
auto-refresh-on-read can never silently rewrite the graph a probe is about to
assert on (the evaluator sets that same variable by hand in the N5 probe, for
the same reason).

The red-probe marker contract -- ``expected_finding_failure`` and
``finding_marker`` -- used to live here and now lives in
:mod:`weld.tests._probe_markers`, which the Node/Next.js readiness corpus
shares (bd lrnx1.1). Import them from there: one decorator and one reader mean
both inventory guards police the same thing, and a re-export here would be a
second spelling of a name with one home.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from weld._federation_endpoints import endpoint_child_name
from weld.tests._field_eval_corpus_fixture import (
    CHILDREN,
    materialize_workspace,
    write_workspaces_yaml,
)
from weld.tests._graph_invariants import graph_edges

#: Every ``wd`` invocation is bounded. Generous: the assertion is never about
#: how long a command took, only that it terminated (test-hygiene "wallclock").
CLI_TIMEOUT_SECONDS = 120


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
    """The pinned environment every ``wd`` subprocess in this suite runs under.

    ``PYTHONPYCACHEPREFIX`` is not hygiene, it is the runtime budget: weld's
    source tree is read-only in a runfiles tree, so without a writable cache
    every one of the ~30 invocations below recompiles the whole package. A
    per-suite cache directory makes that a first-call cost instead of a
    per-call one.
    """
    inherited = [
        os.path.abspath(entry)
        for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep)
        if entry
    ]
    return {
        "PATH": "/usr/bin:/usr/local/bin:/bin",
        "HOME": str(home),
        "PYTHONPATH": os.pathsep.join([weld_import_root(), *inherited]),
        "PYTHONPYCACHEPREFIX": str(home / "pycache"),
        "PYTHONHASHSEED": "0",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "WELD_TELEMETRY": "off",
        "WELD_AUTO_REFRESH": "0",
        "WELD_SOURCE_CHECKOUT_NOTICE": "off",
    }


class CliResult:
    """One ``wd`` invocation: what it printed and how it exited."""

    __slots__ = ("args", "returncode", "stdout", "stderr")

    def __init__(self, args: list[str], proc: subprocess.CompletedProcess) -> None:
        self.args = args
        self.returncode = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr

    @property
    def output(self) -> str:
        return f"{self.stdout}\n{self.stderr}"

    def json(self) -> dict:
        """Parse stdout as JSON, failing loudly with the command that ran."""
        try:
            return json.loads(self.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"`wd {' '.join(self.args)}` did not print JSON "
                f"(rc={self.returncode}): {exc}\n{self.output}"
            ) from exc

    def check(self) -> "CliResult":
        if self.returncode != 0:
            raise AssertionError(
                f"`wd {' '.join(self.args)}` failed (rc={self.returncode}):"
                f"\n{self.output}"
            )
        return self

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CliResult {self.args} rc={self.returncode}>"


class FieldEvalWorkspace:
    """The evaluator's workspace on disk, plus a real ``wd`` runner."""

    def __init__(self, root: Path, home: Path) -> None:
        self.root = Path(root)
        self.env = cli_env(home)

    # -- construction ----------------------------------------------------

    @classmethod
    def materialize(
        cls,
        tmp: Path,
        *,
        cross_repo_strategies: tuple[str, ...] = (),
    ) -> "FieldEvalWorkspace":
        """Lay the workspace down under *tmp*, always with real git repos.

        ``git=True`` is not optional here: every child's lifecycle state comes
        from git, so without it the ledger calls every child ``missing`` and
        the federated route the probes exercise never runs.
        """
        home = Path(tmp) / "home"
        home.mkdir(parents=True, exist_ok=True)
        root = materialize_workspace(
            Path(tmp) / "ws",
            git=True,
            cross_repo_strategies=cross_repo_strategies,
        )
        return cls(root, home)

    # -- running the CLI -------------------------------------------------

    def wd(self, *args: str, cwd: Path | str | None = None) -> CliResult:
        """Run ``python -m weld <args>``; never raises on a non-zero exit."""
        argv = [str(a) for a in args]
        proc = subprocess.run(
            [sys.executable, "-m", "weld", *argv],
            cwd=str(cwd if cwd is not None else self.root),
            env=self.env,
            capture_output=True,
            text=True,
            input="",
            timeout=CLI_TIMEOUT_SECONDS,
        )
        return CliResult(argv, proc)

    def git(self, *args: str, cwd: Path | str | None = None) -> CliResult:
        argv = [str(a) for a in args]
        proc = subprocess.run(
            ["git", *argv],
            cwd=str(cwd if cwd is not None else self.root),
            env=self.env,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT_SECONDS,
        )
        return CliResult(argv, proc)

    # -- the bootstrap ---------------------------------------------------

    def assert_cli_is_this_checkout(self) -> None:
        """The subprocess must import weld from the tree under test.

        A ``python -m weld`` that resolves to some *other* installed weld runs
        green and proves nothing about this branch -- the failure mode the
        source-checkout notice exists to catch, and which this suite silences
        for clean stderr. So it is asserted instead, once, before the bootstrap.
        """
        proc = subprocess.run(
            [sys.executable, "-c", "import weld; print(weld.__file__)"],
            cwd=str(self.root),
            env=self.env,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT_SECONDS,
        )
        expected = (Path(weld_import_root()) / "weld").resolve()
        loaded = Path(proc.stdout.strip() or "/nonexistent").parent.resolve()
        if proc.returncode != 0 or loaded != expected:
            raise AssertionError(
                f"the CLI subprocess imports weld from {loaded}, not the tree "
                f"under test ({expected}); rc={proc.returncode}\n{proc.stderr}"
            )

    def bootstrap(self) -> None:
        """Port of ``bootstrap-fixture.sh``: init + discover per child, then root."""
        self.assert_cli_is_this_checkout()
        for _name, rel in CHILDREN:
            child = self.root / rel
            self.wd("init", cwd=child).check()
            self.discover(cwd=child)
        self.discover()

    def discover(self, cwd: Path | str | None = None) -> CliResult:
        return self.wd(
            "discover", "--safe", "--output", ".weld/graph.json", cwd=cwd
        ).check()

    def set_strategies(
        self, *strategies: str, respect_gitignore: bool = False
    ) -> None:
        """Rewrite ``workspaces.yaml`` and rebuild the root graph from it.

        This is the ``sed`` the evaluator's probes run between findings, in
        the one place both the enable and the restore go through. The
        rediscover is not optional: leaving a root graph on disk that its own
        config no longer describes is a state no probe should ever read.
        """
        write_workspaces_yaml(
            self.root,
            cross_repo_strategies=strategies,
            respect_gitignore=respect_gitignore,
        )
        self.discover()

    # -- reading what it wrote -------------------------------------------

    def graph(self, rel: str | None = None) -> dict:
        """Load ``.weld/graph.json`` for the root, or for child path *rel*."""
        base = self.root if rel is None else self.root / rel
        return json.loads((base / ".weld" / "graph.json").read_text(encoding="utf-8"))

    def child_graphs(self) -> dict[str, dict]:
        """``{child name: graph payload}`` -- the map ``assert_edges_resolve`` wants."""
        return {name: self.graph(rel) for name, rel in CHILDREN}

    def config_text(self, rel: str) -> str:
        return (self.root / rel / ".weld" / "discover.yaml").read_text(encoding="utf-8")


def cross_repo_joins(root_graph: dict) -> set[tuple[str, str, str]]:
    """``{(from-child, to-child, package)}`` for every cross-repo edge.

    Endpoints are read child-name-first and spelling-agnostically, through the
    helper that knows both shapes (ADR 0137 ss1): the probes that use this ask
    *which repos got joined*, and reading it through an id convention would
    make them fail on a spelling rather than on the join they exist to pin.
    """
    return {
        (
            str(endpoint_child_name(str(edge.get("from")))),
            str(endpoint_child_name(str(edge.get("to")))),
            str((edge.get("props") or {}).get("package")),
        )
        for edge in graph_edges(root_graph)
    }


def callers_in_graph(graph: dict, target: str) -> set[str]:
    """Every symbol the graph records as calling *target*.

    What the graph *attributes*, which a probe compares against what ``wd
    callers`` reports for the same function -- the two are not the same thing
    when one function has more than one identity (finding M2).
    """
    return {
        str(edge.get("from"))
        for edge in graph_edges(graph)
        if edge.get("type") == "calls" and edge.get("to") == target
    }
