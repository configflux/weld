"""Drive the Node/Next.js readiness corpus through the **real** ``wd`` CLI.

Every gap ADR 0142 records was found by running ``python -m weld`` against a
workspace on disk with real TypeScript and JavaScript grammars loaded, and
every one of them lives on a route no in-process test enters -- ``wd init``'s
generator, the tree-sitter dispatch, the express regex pass, the cross-repo
manifest scan. So the probes shell the CLI the same way the field-eval harness
does: a subprocess per command, one workspace per module, and the graphs the
product itself writes.

**Grammars are a hard requirement here, not a skip.** Under Bazel they arrive
as declared deps (``@pypi//tree_sitter`` plus the TypeScript and JavaScript
grammars), so their absence means a broken environment rather than a modest
one -- and a corpus whose whole point is "no CI lane ever loaded a real TS
grammar" is the last place to put a self-skip that could quietly make the lane
assert nothing. :func:`assert_grammars_available` therefore *fails*, naming
what is missing and how to supply it. A by-hand run from an interpreter
without the grammars points ``WELD_NODE_EVAL_PYTHON`` at one that has them
(the ``WELD_FIELD_EVAL_PYTHON`` idiom); that variable selects the interpreter
every ``wd`` subprocess runs under, so the check and the run cannot disagree.

Environment is pinned rather than inherited -- fixed ``PYTHONHASHSEED`` / TZ /
locale, ``HOME`` redirected into the tempdir so no ambient git or weld config
is read, telemetry off, and ``WELD_AUTO_REFRESH=0`` so auto-refresh-on-read
(ADR 0051) can never rewrite the graph a probe is about to assert on.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from weld.tests._graph_invariants import graph_edges, graph_nodes
from weld.tests._node_eval_corpus import (
    POLYREPO_CHILDREN,
    materialize_monorepo,
    materialize_polyrepo,
    write_wired_config,
)

#: Every ``wd`` invocation is bounded. Generous: the assertion is never about
#: how long a command took, only that it terminated.
CLI_TIMEOUT_SECONDS = 120

#: The grammars the corpus needs loaded for the CLI to see TypeScript and
#: JavaScript as syntax rather than as text. ``tree_sitter_javascript`` is
#: required even though nothing loads it today: gap G6's fix will, and a lane
#: that acquired the grammar only at fix time would have been unproven until
#: then.
REQUIRED_GRAMMAR_MODULES = (
    "tree_sitter",
    "tree_sitter_typescript",
    "tree_sitter_javascript",
)


def node_eval_python() -> str:
    """The interpreter every ``wd`` subprocess runs under.

    ``sys.executable`` under Bazel, where the grammars ride in as deps. A
    by-hand run from a bare system python points ``WELD_NODE_EVAL_PYTHON`` at
    a grammar-capable interpreter instead -- the same override the field-eval
    bundle takes as ``WELD_FIELD_EVAL_PYTHON``.
    """
    return os.environ.get("WELD_NODE_EVAL_PYTHON") or sys.executable


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
    """The fixed environment every ``wd`` subprocess in this suite runs under.

    Determinism first (ADR 0012): a hash seed, a locale and a timezone the
    probes can rely on, ``HOME`` redirected into the tempdir so no ambient git
    or weld config is read, and ``WELD_AUTO_REFRESH=0`` so auto-refresh-on-read
    (ADR 0051) cannot rewrite a graph mid-probe.

    ``PYTHONPATH`` is inherited and extended rather than replaced: under Bazel
    it is how the grammar wheels reach the subprocess at all.
    ``PYTHONPYCACHEPREFIX`` is not hygiene but the runtime budget -- weld's
    source tree is read-only in a runfiles tree, so without a writable cache
    every invocation recompiles the whole package.
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


class NodeEvalWorkspace:
    """A corpus workspace on disk, plus a real ``wd`` runner."""

    def __init__(self, root: Path, home: Path) -> None:
        self.root = Path(root)
        self.env = cli_env(home)

    # -- construction ----------------------------------------------------

    @classmethod
    def monorepo(cls, tmp: Path) -> "NodeEvalWorkspace":
        """The npm-workspaces monorepo, versioned, with no ``.weld`` yet."""
        home = Path(tmp) / "home"
        home.mkdir(parents=True, exist_ok=True)
        return cls(materialize_monorepo(Path(tmp) / "mono"), home)

    @classmethod
    def polyrepo(cls, tmp: Path) -> "NodeEvalWorkspace":
        """The two-repo Node polyrepo, resolver wired, children uninitialised."""
        home = Path(tmp) / "home"
        home.mkdir(parents=True, exist_ok=True)
        return cls(materialize_polyrepo(Path(tmp) / "poly"), home)

    # -- running the CLI -------------------------------------------------

    def wd(self, *args: str, cwd: Path | str | None = None) -> CliResult:
        """Run ``python -m weld <args>``; never raises on a non-zero exit."""
        argv = [str(a) for a in args]
        proc = subprocess.run(
            [node_eval_python(), "-m", "weld", *argv],
            cwd=str(cwd if cwd is not None else self.root),
            env=self.env,
            capture_output=True,
            text=True,
            input="",
            timeout=CLI_TIMEOUT_SECONDS,
        )
        return CliResult(argv, proc)

    # -- preconditions ---------------------------------------------------

    def assert_cli_is_this_checkout(self) -> None:
        """The subprocess must import weld from the tree under test.

        A ``python -m weld`` that resolves to some *other* installed weld runs
        green and proves nothing about this branch -- the failure mode the
        source-checkout notice exists to catch, and which this suite silences
        for clean stderr. So it is asserted instead, once, before any probe.
        """
        proc = subprocess.run(
            [node_eval_python(), "-c", "import weld; print(weld.__file__)"],
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

    def assert_grammars_available(self) -> None:
        """Fail -- never skip -- when the CLI interpreter has no TS/JS grammar.

        The whole finding behind this corpus is that no lane ever loaded a
        real TypeScript grammar, so a silent skip here would reproduce the
        defect in the gate meant to catch it. Under Bazel the grammars are
        declared deps and this can only fire on a broken target; by hand it
        tells the reader exactly which interpreter to point at.
        """
        missing = [
            module
            for module in REQUIRED_GRAMMAR_MODULES
            if subprocess.run(
                [node_eval_python(), "-c", f"import {module}"],
                env=self.env,
                capture_output=True,
                text=True,
                timeout=CLI_TIMEOUT_SECONDS,
            ).returncode
            != 0
        ]
        if missing:
            raise AssertionError(
                f"{node_eval_python()} cannot import {', '.join(missing)}. "
                "This corpus asserts what weld sees with real TypeScript and "
                "JavaScript grammars loaded, so it fails rather than skips. "
                "Under Bazel the grammars are declared deps of the target -- "
                "check them. By hand, point WELD_NODE_EVAL_PYTHON at an "
                "interpreter that has tree-sitter, tree-sitter-typescript and "
                "tree-sitter-javascript installed."
            )

    # -- the bootstraps --------------------------------------------------

    def bootstrap_init(self) -> CliResult:
        """``wd init`` and nothing else -- what gap G1 reads."""
        self.assert_cli_is_this_checkout()
        self.assert_grammars_available()
        return self.wd("init").check()

    def bootstrap_wired(self) -> None:
        """``wd init``, then the generous hand-wiring, then ``wd discover``.

        The overwrite is deliberate and is documented on
        :data:`weld.tests._node_eval_corpus.WIRED_DISCOVER_YAML`: probes for
        gaps G2-G7 must be red for their own reason, not because ``wd init``
        never wired the file they read (which is gap G1).
        """
        self.bootstrap_init()
        write_wired_config(self.root)
        self.discover()

    def bootstrap_federated(self) -> None:
        """``wd init`` + ``wd discover`` in each child, then a root discover."""
        self.assert_cli_is_this_checkout()
        self.assert_grammars_available()
        for _name, rel in POLYREPO_CHILDREN:
            child = self.root / rel
            self.wd("init", cwd=child).check()
            self.discover(cwd=child)
        self.discover()

    def discover(self, cwd: Path | str | None = None) -> CliResult:
        return self.wd(
            "discover", "--safe", "--output", ".weld/graph.json", cwd=cwd
        ).check()

    # -- reading what it wrote -------------------------------------------

    def graph(self, rel: str | None = None) -> dict:
        """Load ``.weld/graph.json`` for the root, or for child path *rel*."""
        base = self.root if rel is None else self.root / rel
        return json.loads((base / ".weld" / "graph.json").read_text(encoding="utf-8"))

    def config_text(self) -> str:
        return (self.root / ".weld" / "discover.yaml").read_text(encoding="utf-8")


# --------------------------------------------------------------- graph views


def node_props(node: Mapping) -> Mapping:
    props = node.get("props")
    return props if isinstance(props, Mapping) else {}


def nodes_of_type(graph: dict, node_type: str) -> dict[str, dict]:
    """Every node of *node_type*, keyed by id."""
    return {
        node_id: node
        for node_id, node in graph_nodes(graph).items()
        if node.get("type") == node_type
    }


def symbols_in_file(graph: dict, rel_path: str) -> dict[str, dict]:
    """Every ``symbol`` node the graph attributes to *rel_path*.

    The ``<file>`` module sentinel is excluded: it is call-graph scaffolding
    that exists for any parsed file, so counting it as a symbol would let a
    probe about *definitions* pass on a file whose definitions were all lost.
    """
    return {
        node_id: node
        for node_id, node in nodes_of_type(graph, "symbol").items()
        if node_props(node).get("file") == rel_path
        and node_props(node).get("qualname") != "<file>"
    }


def edges_from(graph: dict, node_id: str) -> list[dict]:
    """Every edge leaving *node_id*."""
    return [edge for edge in graph_edges(graph) if edge.get("from") == node_id]


def file_node_id(graph: dict, rel_path: str) -> str:
    """The id of the ``file`` node for *rel_path*, or a loud assertion."""
    hits = sorted(
        node_id
        for node_id, node in nodes_of_type(graph, "file").items()
        if node_props(node).get("file") == rel_path
    )
    if len(hits) != 1:
        raise AssertionError(
            f"expected exactly one file node for {rel_path}, got {hits}"
        )
    return hits[0]
