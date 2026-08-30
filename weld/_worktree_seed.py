"""Bootstrap a fresh checkout's ``.weld/`` state on first read (ADR 0096 §2).

A checkout can hold a perfectly good graph and still be unable to say
anything about it -- or hold no graph at all while an identical one sits
in a sibling checkout of the same repository. Both are the same failure
from a user's seat: the first ``wd query`` in a new worktree either
answers from a graph it cannot date, or refuses to answer and sends them
to grep.

Mode B (``wd init --track-graphs``) commits ``.weld/graph.json``
precisely so every clone shares a pre-built graph, but the
``graph-meta.json`` sidecar that carries ``git_sha`` stays gitignored
(:mod:`weld._gitignore_writer`). A fresh clone or linked worktree
therefore arrives with the graph and no basis for it:
:func:`weld._staleness.compute_stale_info` reads a missing ``git_sha`` as
``source_stale``, and with no ``discovery-state.json`` the refresh that
follows is a *full* rediscover. Mode B paid exactly the cold cost it
exists to avoid. Mode A (the ADR 0076 default) gitignores the graph
outright, so a fresh worktree starts with nothing -- even though the
checkout it was branched from usually holds a graph that is one
incremental pass away from correct.

:func:`ensure_seeded` repairs both at the single choke point every
graph-backed read already passes through
(:func:`weld._graph_cli.ensure_graph_exists`), deliberately **not**
inside :mod:`weld._auto_refresh` -- auto-refresh is never reached when
the graph is missing, which is exactly the Mode A case.

Three properties make this safe to run on the read path:

* **Conservative basis.** Mode B's synthesized ``git_sha`` is the last
  commit that touched the tracked graph, which is at or before the commit
  the graph actually describes. Derived staleness can therefore only
  over-trigger a refresh; it can never report a stale graph as fresh.
* **Proven-matching state.** Derived state is copied from another
  checkout only against proof that it describes our graph -- see
  :mod:`weld._worktree_seed_copy`, which owns every cross-checkout copy.
* **Nothing is trusted for long.** A Mode A seed is immediately
  reconciled against the worktree's own tree
  (:mod:`weld._worktree_seed_mode_a`), so a cross-branch or dirty-source
  seed self-corrects on the read that created it.

Everything here degrades to a no-op rather than raising: a read must not
fail because a bootstrap optimization could not run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# The gate-freeze predicate is imported rather than restated: WELD_AUTO_REFRESH
# is a contract (ADR 0051, pinned by run_local_task_gate_freeze_test.sh), and a
# second copy of its accepted spellings is exactly how such a contract drifts.
from weld._auto_refresh import _env_disabled
from weld._git_worktree import (
    graph_is_tracked,
    is_linked_worktree,
    tracked_graph_commit,
)
from weld._graph_meta_sidecar import (
    SIDECAR_VERSION,
    read_meta_for_staleness,
    sidecar_path_for,
)
from weld._worktree_seed_copy import (
    GRAPH_NAME,
    SEED_STATE_FILES,
    borrow_state_from_identical_sibling,
)
from weld._worktree_seed_inventory import synthesize_coverage_inventory
from weld._worktree_seed_mode_a import copy_seed_worktree
from weld.workspace_state import atomic_write_text

__all__ = ["SEED_STATE_FILES", "ensure_seeded", "seed_blocked_reason"]

_WORKSPACES_NAME = "workspaces.yaml"
_DISCOVER_CONFIG_NAME = "discover.yaml"


def ensure_seeded(root: Path | str, *, no_refresh: bool = False) -> dict | None:
    """Bootstrap ``.weld/`` state at *root* if it is missing and derivable.

    Returns a summary dict when something was written -- ``action``, the
    ``git_sha`` stamped into the sidecar, the ``seeded_state`` basenames
    copied, and (gate 4) the ``coverage_inventory`` file count synthesized;
    gate 5 adds ``source`` and ``reconciled`` -- or ``None`` when no gate
    opened, which is the overwhelmingly common case.

    Gates, in order (ADR 0096 §2):

    1. Refresh frozen -> ``None``, by either of the two spellings
       ADR 0051 gives that freeze: the ``WELD_AUTO_REFRESH`` env var or
       the caller's ``--no-refresh``. Seeding writes ``.weld/``, so the
       freeze that stops a read from rewriting tracked artifacts must
       cover it too -- and the flag ADR 0051 hands pure read-only callers
       for exactly that purpose has to mean it here as well. Gate 5 makes
       the point sharpest: it ends in an unconditional reconcile, a
       discovery pass under the one flag whose name refuses one.
    2. ``graph.json`` and ``graph-meta.json`` both present -> ``None``.
       This is the steady state, and its cost is the two ``stat`` calls
       above -- nothing further runs on a warm root.
    3. Federated root -> ``None``. Polyrepo worktree reads are explicitly
       out of scope for ADR 0096.
    4. Graph present -> Mode B bootstrap, :func:`_bootstrap_mode_b`.
    5. Graph missing -> Mode A copy-seed, :func:`_copy_seed_worktree`.

    A frozen read is not left worse off than it was before seeding
    existed: Mode B still answers from its tracked graph (reporting
    source-stale, which is what ``--no-refresh`` warns about anyway), and
    Mode A keeps the first-run guidance. Declining is silent because gate
    1's other spelling is, and the whole point is that they match.
    """
    root = Path(root)
    if no_refresh or _env_disabled(os.environ):
        return None
    weld_dir = root / ".weld"
    graph_path = weld_dir / GRAPH_NAME
    if graph_path.is_file() and sidecar_path_for(graph_path).is_file():
        return None
    if (weld_dir / _WORKSPACES_NAME).is_file():
        return None
    if not graph_path.is_file():
        return _copy_seed_worktree(root, graph_path)
    return _bootstrap_mode_b(root, graph_path)


def _copy_seed_worktree(root: Path, graph_path: Path) -> dict | None:
    """Seed a fresh **linked worktree** from a sibling checkout (gate 5).

    Two preconditions beyond "no graph", cheapest first:

    * ``discover.yaml`` present. A seed is only useful if this checkout
      can re-derive from it, and requiring the config rather than
      copying it is what keeps configuration inside the tree that owns
      it: strategy selection never arrives from another checkout.
    * *root* is a linked worktree. This is the population ADR 0096 is
      for -- a checkout that shares a repository with a sibling holding
      a graph. A plain clone has no sibling to seed from and keeps
      today's first-run guidance, with ``wd warm`` as the answer there;
      the main checkout is where a graph is *made*, so a missing graph
      there means the user has simply not run ``wd discover`` yet, and
      silently materializing one from some other worktree would hide
      that.

    Both are cheap enough to sit on the read path only because they run
    when there is no graph at all -- a state that ends with either a
    seed or an error message, never a served read.
    """
    if not (root / ".weld" / _DISCOVER_CONFIG_NAME).is_file():
        return None
    if not is_linked_worktree(root):
        return None
    return copy_seed_worktree(root, graph_path)


#: Why a graphless linked worktree could not be seeded, in the user's own
#: terms. Hard-wrapped rather than left to the terminal: it is printed
#: above a guidance block whose other lines are short, and a single long
#: paragraph reflowed by an 80-column terminal reads as noise between them.
_NO_CONFIG_REASON = (
    "This is a linked git worktree with no .weld/discover.yaml, so no graph\n"
    "could be seeded from a sibling checkout: seeding always reads the\n"
    "worktree's own config, and git puts that file here only when the\n"
    "repository tracks it. Track it (git add -f .weld/discover.yaml) to\n"
    "enable seeding in every worktree of this repository."
)


def seed_blocked_reason(root: Path | str) -> str | None:
    """Name the gate-5 precondition a graphless checkout is missing.

    The inverse of :func:`_copy_seed_worktree`, and here rather than in the
    CLI for that reason: the preconditions and the sentence explaining them
    have to move together, or the message outlives the rule it describes.

    Only one decline is worth explaining. Field eval 0.23.1 finding 09
    reported a repository that gitignores all of ``.weld/`` -- the shape
    ``wd init --ignore-all`` writes, and a common hand-rolled choice -- so
    every linked worktree of it arrives with no config, seeding can never
    fire, and the user sees the ordinary first-run guidance with nothing in
    it about the one thing they would have to change. The remedy is a
    repository-wide policy decision, not something a reader guesses from
    "No Weld graph found."

    ``None`` -- meaning "say nothing beyond the usual guidance" -- for every
    other decline, each of which the standing message already serves:

    * config present. Whatever stopped the seed, it was not this; a plain
      ``wd discover`` is still the answer.
    * federated root, which gate 3 declined before gate 5 was ever reached
      (ADR 0096 puts polyrepo worktree reads out of scope), so claiming a
      config prerequisite here would name a cause that never applied.
    * not a linked worktree. A clone or the main checkout has no sibling to
      seed from in the first place, so nothing was withheld from it.
    """
    root = Path(root)
    weld_dir = root / ".weld"
    if (weld_dir / _DISCOVER_CONFIG_NAME).is_file():
        return None
    if (weld_dir / _WORKSPACES_NAME).is_file():
        return None
    if not is_linked_worktree(root):
        return None
    return _NO_CONFIG_REASON


def _bootstrap_mode_b(root: Path, graph_path: Path) -> dict | None:
    """Give a tracked graph the two derived states git did not carry (gate 4).

    A Mode B checkout commits ``graph.json`` and gitignores everything that
    explains it, so a clone arrives missing *both* halves of freshness: the
    ``git_sha`` basis the ADR 0017 signals compare against, and the inventory
    ADR 0101's coverage probe compares against. Repairing only the first was
    the bd r7d7 defect -- the clone then reported a confident ``fresh`` while
    a file in scope at HEAD sat outside the graph, invisible to all three
    signals at once.

    Three steps, each independently gated, in the order their evidence
    outranks each other:

    1. borrow a sibling checkout's real state when one can be proven to
       describe our exact graph bytes -- a recorded inventory beats a
       derived one, and it is what keeps a linked worktree's reconcile
       incremental;
    2. otherwise derive the coverage claim from the graph's own anchors
       (:func:`weld._worktree_seed_inventory.synthesize_coverage_inventory`);
    3. stamp the staleness basis (:func:`_record_tracked_basis`).

    The inventory is written **before** the basis on purpose. Gate 2 closes
    on the sidecar's existence, so a basis landing first would shut the door
    on a checkout whose inventory write had failed, and the hole would then
    outlive every later read.

    ``None`` when nothing was written at all, including the whole of Mode A:
    an untracked graph has neither a commit to derive a basis from nor any
    claim to a bootstrap here, and gate 5 owns it end to end.
    """
    if not graph_is_tracked(root):
        return None
    seeded_state = borrow_state_from_identical_sibling(root, graph_path)
    coverage = synthesize_coverage_inventory(root, graph_path)
    git_sha = _record_tracked_basis(root, graph_path)
    if git_sha is None and coverage is None and not seeded_state:
        return None
    return {
        "action": "mode_b_bootstrap",
        "git_sha": git_sha,
        "seeded_state": seeded_state,
        "coverage_inventory": coverage,
    }


def _record_tracked_basis(root: Path, graph_path: Path) -> str | None:
    """Stamp a minimal sidecar for a tracked graph that has no basis at all.

    Mirrors :func:`weld.warm._write_sidecar`: the same minimal
    ``{version, git_sha}`` envelope, written atomically, for the same
    reason -- a graph that did not come from a local discover carries no
    volatile meta and would otherwise read as source-stale forever.

    ``None`` when the graph cannot be read at all (a corrupt graph gets no
    basis: recording one would only mask it from the refresh that might
    replace it), when the tracked path has no history, when the write fails,
    or -- crucially -- when a basis is **already recorded**.

    That last condition is why the precondition is "no ``git_sha``
    anywhere", not "no sidecar file". A pre-ADR-0065 graph keeps
    ``git_sha`` inside ``graph.json`` and has no sidecar at all, and that
    value is the *exact* commit the graph was built from. The
    approximation here is only conservative when the graph was committed
    as of the content it describes; a graph generated at commit A and
    committed later at commit B answers B, which would report a stale
    graph as fresh -- the one failure mode ADR 0096 forbids. Deferring to
    any recorded basis removes the hazard, and it costs nothing that
    matters: the bug being fixed is precisely ``git_sha is None``, where
    no better answer exists.

    Declining here does not decline the coverage inventory beside it: the
    two answer different questions, and a graph that knows its own build
    commit still ships no account of what it read.

    Reading the basis parses ``graph.json`` when no sidecar mirror can
    serve it, which is why it sits behind the cheap tracked-graph probe
    and behind gate 2 -- it runs once for a fresh checkout, never on a
    warm root.
    """
    recorded = read_meta_for_staleness(graph_path)
    if recorded is None or recorded.get("git_sha") is not None:
        return None
    git_sha = tracked_graph_commit(root)
    if git_sha is None:
        return None
    payload = {"version": SIDECAR_VERSION, "git_sha": git_sha}
    try:
        atomic_write_text(
            sidecar_path_for(graph_path),
            json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        )
    except OSError:
        return None
    return git_sha
