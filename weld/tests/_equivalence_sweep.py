#!/usr/bin/env python3
"""Full-vs-incremental equivalence sweep over generated repos (ADR 0139 § 5).

:mod:`weld.tests._equivalence_sweep_repo` draws a small multi-glob Python repo
and a mutation round from one seed. This module runs that case through the real
``weld.discover._discover_single_repo`` twice -- once as a full discover of the
mutated tree, once as an incremental refresh over a seeded graph of the intact
tree -- and diffs the two node-id and edge-triple sets.

Node and edge *sets*, deliberately, not whole-graph equality. The enumerative
family compares stripped graph dicts because each of its fixtures is three files
wide and a whole-dict diff is readable; a generated case is not, and the failures
this exists to find are a node or an edge that one path has and the other does
not. Prop-level drift is out of scope here and stays with the hand-written
members (ADR 0139 § 5 scopes this to node and edge sets).

Two properties make a run usable as evidence:

* **Deterministic.** The same seed produces a byte-identical rendered report.
  Node ids are root-relative, so the temporary directory never reaches the
  output, and every set is sorted before it is rendered.
* **Reproducible by hand.** Every report carries its seed and a command that
  re-runs exactly that case, so a sweep failure on the merge-train lane
  (bd ``us0u9``) hands over a one-line repro rather than a log to re-derive.

A finding here is *not* proven by this harness going green afterwards. Per
ADR 0113 the artifact of a divergence is a pinned seed case in the enumerative
family (bd ``br7jb``); a generator that stops reporting proves nothing on its
own, which is why :func:`run_case` carries the ``sabotage`` seam below and why
bd ``us0u9`` keeps a permanent negative control over it.

Standalone, from the repository root (``PYTHONPATH=.`` is what puts ``weld`` on
the path -- running the file directly only puts ``weld/tests`` there)::

    PYTHONPATH=. python3 weld/tests/_equivalence_sweep.py --seed 1
    PYTHONPATH=. python3 weld/tests/_equivalence_sweep.py --base-seed 1 --count 50

Exit status is 1 when any case diverged, 0 otherwise.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from weld import discover as discover_module
from weld.tests._equivalence_sweep_repo import Case, files_after, files_before
from weld.tests._equivalence_sweep_repo import generate_case, sources_yaml

#: How a report tells a reader to reproduce itself. Kept next to the argument
#: parser below so the two cannot drift into naming different flags.
REPRO_TEMPLATE = "PYTHONPATH=. python3 weld/tests/_equivalence_sweep.py --seed {seed}"

Edge = tuple[str, str, str]
Sabotage = Callable[[dict], None]


class SweepSelfCheckError(RuntimeError):
    """A run that could not have meant anything, refused instead of reported.

    Both ways this harness can be silently vacuous raise this rather than
    returning an EQUIVALENT verdict: the incremental round degrading to a
    second full discover (two identical paths always agree), and a generated
    tree discovery never saw (two empty graphs always agree). Neither is a
    divergence, so neither belongs in a :class:`DiffReport`; both are the
    harness failing, and a harness that reports green while failing is exactly
    the escape class ADR 0139 exists to close.
    """


@dataclass(frozen=True)
class DiffReport:
    """One case's verdict, rendered byte-identically for a given seed."""

    seed: int
    summary: str
    full_population: tuple[int, int]
    nodes_only_full: tuple[str, ...]
    nodes_only_incremental: tuple[str, ...]
    edges_only_full: tuple[Edge, ...]
    edges_only_incremental: tuple[Edge, ...]

    @property
    def divergent(self) -> bool:
        return bool(
            self.nodes_only_full
            or self.nodes_only_incremental
            or self.edges_only_full
            or self.edges_only_incremental
        )

    @property
    def repro(self) -> str:
        return REPRO_TEMPLATE.format(seed=self.seed)

    def render(self) -> str:
        verdict = "DIVERGENT" if self.divergent else "EQUIVALENT"
        nodes, edges = self.full_population
        lines = [
            f"seed {self.seed}: {verdict}",
            f"  case: {self.summary}",
            f"  full graph: {nodes} nodes, {edges} edges",
            f"  repro: {self.repro}",
        ]
        lines += _render_block("nodes only in full", self.nodes_only_full)
        lines += _render_block(
            "nodes only in incremental", self.nodes_only_incremental
        )
        lines += _render_block(
            "edges only in full", [_render_edge(e) for e in self.edges_only_full]
        )
        lines += _render_block(
            "edges only in incremental",
            [_render_edge(e) for e in self.edges_only_incremental],
        )
        return "\n".join(lines) + "\n"


def _render_edge(edge: Edge) -> str:
    source, target, kind = edge
    return f"{source} -> {target} [{kind}]"


def _render_block(label: str, items: Sequence[str]) -> list[str]:
    lines = [f"  {label} ({len(items)}):"]
    lines += [f"    {item}" for item in items]
    return lines


def node_ids(graph: dict) -> set[str]:
    return set(graph.get("nodes", {}))


def edge_triples(graph: dict) -> set[Edge]:
    return {(e["from"], e["to"], e["type"]) for e in graph.get("edges", [])}


def compare(case: Case, full: dict, incremental: dict) -> DiffReport:
    """Diff two real discovery outputs into a report. Pure, no I/O."""
    full_nodes, incremental_nodes = node_ids(full), node_ids(incremental)
    full_edges, incremental_edges = edge_triples(full), edge_triples(incremental)
    return DiffReport(
        seed=case.seed,
        summary=case.summary(),
        full_population=(len(full_nodes), len(full_edges)),
        nodes_only_full=tuple(sorted(full_nodes - incremental_nodes)),
        nodes_only_incremental=tuple(sorted(incremental_nodes - full_nodes)),
        edges_only_full=tuple(sorted(full_edges - incremental_edges)),
        edges_only_incremental=tuple(sorted(incremental_edges - full_edges)),
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "sweep@example.invalid")
    _git(root, "config", "user.name", "Equivalence Sweep")
    _git(root, "config", "commit.gpgsign", "false")


def _materialize(root: Path, case: Case, files: dict[str, str]) -> None:
    """Write *files* under *root*, removing generated files no longer present."""
    for path in sorted(files):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(files[path], encoding="utf-8")
    keep = set(files)
    for name in case.roots:
        for existing in sorted((root / name).rglob("*.py")):
            if existing.relative_to(root).as_posix() not in keep:
                existing.unlink()
    _prune_empty_dirs(root, case.roots)
    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(sources_yaml(case), encoding="utf-8")


def _prune_empty_dirs(root: Path, names: Iterable[str]) -> None:
    for name in names:
        base = root / name
        if not base.is_dir():
            continue
        for directory in sorted(base.rglob("*"), reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        if not any(base.iterdir()):
            base.rmdir()


def _commit(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "sweep")


def _discover(root: Path, *, incremental: bool) -> dict:
    return discover_module._discover_single_repo(
        root, incremental=incremental, write_graph=True
    )


def _refresh_incrementally(root: Path) -> dict:
    """Refresh incrementally, refusing to return a silent full discover.

    ``_discover_single_repo`` degrades to a full discover on its own whenever
    the basis is invalid or the delta says nothing changed, and says so nowhere
    in the graph it returns. A harness that compared two full discovers would
    agree on every seed forever, so the branch is *observed* rather than
    assumed: the incremental merge is the only step the full path never runs.

    Single-threaded by construction: the swap below is process-global for the
    duration of one discover, which is all this harness ever runs at a time.
    """
    calls: list[int] = []
    original = discover_module.run_incremental_merge

    def observed(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    discover_module.run_incremental_merge = observed
    try:
        graph = _discover(root, incremental=True)
    finally:
        discover_module.run_incremental_merge = original
    if not calls:
        raise SweepSelfCheckError(
            "incremental refresh never reached run_incremental_merge -- the "
            "round changed nothing, or the incremental basis was rejected"
        )
    return graph


def _check_population(case: Case, graph: dict) -> None:
    """Every generated module that discovery should anchor is in the graph.

    A misdrawn glob or a generator that stopped writing files would leave two
    empty graphs agreeing perfectly. ``__init__.py`` is excluded because the
    canonical Python trio deliberately leaves it unanchored.
    """
    expected = {
        f"file:{path[: -len('.py')]}"
        for path in files_after(case)
        if not path.endswith("__init__.py")
    }
    missing = sorted(expected - node_ids(graph))
    if missing:
        raise SweepSelfCheckError(
            f"seed {case.seed}: generated modules absent from the full "
            f"discover: {', '.join(missing)}"
        )


def full_graph(case: Case) -> dict:
    """A full discover of the mutated tree, from nothing."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _init_repo(root)
        _materialize(root, case, files_after(case))
        _commit(root)
        graph = _discover(root, incremental=False)
    _check_population(case, graph)
    return graph


def incremental_graph(case: Case) -> dict:
    """Seed a full discover of the intact tree, apply the round, refresh."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _init_repo(root)
        _materialize(root, case, files_before(case))
        _commit(root)
        _discover(root, incremental=False)
        _materialize(root, case, files_after(case))
        _commit(root)
        return _refresh_incrementally(root)


def run_case(case: Case, *, sabotage: Sabotage | None = None) -> DiffReport:
    """Run both discovery paths for *case* and diff them.

    *sabotage* mutates the incremental graph in place before the comparison. It
    is the negative-control seam ADR 0139 § 5 and bd ``us0u9`` require: a sweep
    whose comparison has quietly stopped comparing is green forever, which is
    mechanism 1 in its purest form, so the only way to trust a green run is to
    be able to prove the same code path goes red on an injected difference.
    Production callers leave it ``None``.
    """
    full = full_graph(case)
    incremental = incremental_graph(case)
    if sabotage is not None:
        sabotage(incremental)
    return compare(case, full, incremental)


def sweep(
    seeds: Iterable[int], *, sabotage: Sabotage | None = None
) -> Iterator[DiffReport]:
    """Run each seed and yield its report as it finishes.

    Lazy rather than list-returning so a long run reports as it goes: bd
    ``us0u9``'s merge-train sweep is a minute or two of seeds, and a divergence
    found in the first ten should not wait on the last thousand -- nor be lost
    with the process if one of them raises.

    *sabotage* is :func:`run_case`'s seam, forwarded rather than re-implemented
    so bd ``us0u9``'s permanent negative control plants its divergence in the
    same function the merge-train window and :func:`main` both call. A control
    that ran its own loop would leave this one uncontrolled -- which is the
    failure it exists to detect.
    """
    for seed in seeds:
        yield run_case(generate_case(seed), sabotage=sabotage)


def _seeds(args: argparse.Namespace) -> list[int]:
    if args.seed:
        return list(args.seed)
    return [args.base_seed + offset for offset in range(args.count)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Full-vs-incremental discovery equivalence sweep."
    )
    parser.add_argument(
        "--seed", type=int, action="append",
        help="run exactly this seed; repeatable. Overrides --base-seed/--count.",
    )
    parser.add_argument("--base-seed", type=int, default=0)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument(
        "--quiet", action="store_true",
        help="print only the cases that diverged.",
    )
    args = parser.parse_args(argv)

    divergent = 0
    for report in sweep(_seeds(args)):
        if report.divergent:
            divergent += 1
        if report.divergent or not args.quiet:
            sys.stdout.write(report.render())
            sys.stdout.flush()
    sys.stdout.write(f"divergent cases: {divergent}\n")
    return 1 if divergent else 0


if __name__ == "__main__":
    raise SystemExit(main())
