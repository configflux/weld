"""Graph-freshness computation (ADR 0017, ADR 0101).

Split out of :mod:`weld.graph` so the ``Graph`` class stays at its
line-count cap while the freshness rules remain directly unit-testable.

Composes all four freshness signals into one verdict: committed diff
(below), the working-tree dimension (:mod:`weld._staleness_worktree`), the
ADR 0101 coverage probe (:mod:`weld._staleness_coverage`), and the reverted-
content probe (:mod:`weld._staleness_reverted`, ADR 0017's fourth
amendment). They answer one question between them and are only correct as a
set -- each signal's own helpers live in its own module (mirroring the
existing :mod:`weld._staleness_worktree` split), but :func:`compute_stale_info`
remains the *sole* composer, so a caller consulting one sibling module
directly is reading a piece of the answer, never the freshness verdict
itself.
"""

from __future__ import annotations

from pathlib import Path

from weld._git import (
    commits_behind as _commits_behind,
    drift_is_graph_only,
    get_git_sha,
    is_git_repo,
    source_files_changed_since,
    working_tree_dirty_sources,
)
from weld._stale_reasons import CHANGED_SINCE_DISCOVERY
from weld._staleness_coverage import coverage_stale, coverage_stale_detail
from weld._staleness_reverted import reverted_content_stale
from weld._staleness_worktree import (
    dirty_sources_diverge,
    dirty_sources_diverge_detail,
)

#: Cap on ``stale_sources`` entries returned by :func:`compute_stale_info`.
#: The diverging set can be the whole inventory after a rebase, so it needs a
#: bound (ADR 0082 bounded-envelope rule); 50 matches the established
#: ``DEFAULT_NEIGHBOR_CAP`` magnitude other agent-facing reads use
#: (:mod:`weld._envelope_diet`) rather than inventing a fresh number.
MAX_STALE_SOURCES: int = 50

#: The top-level ``reason`` a root that holds no graph at all answers with
#: (bd 0nqy). Named because a second consumer arrived: the ADR 0100 amendment
#: (bd kgx83) hangs ``seed_blocked_reason`` off exactly this state in
#: :mod:`weld._stale_payload`, and a literal copied there would let the two
#: drift on spelling with nothing to catch it. Distinct from the
#: ``stale_sources`` vocabulary in :mod:`weld._stale_reasons`, which answers
#: "which file diverged, and why" -- this one answers "why is there no
#: freshness verdict to give at all".
NO_GRAPH_REASON = "no graph"

#: The top-level ``reason`` for a graph condemned by the working-tree signal
#: on evidence that signal cannot enumerate (ADR 0141 D1). ADR 0134's rule --
#: a verdict nobody can act on is worse than no verdict -- has a staleness
#: instance, and field-eval finding M1 was it: ``stale: yes`` with an empty
#: ``stale_sources``, every other field reassuring, and the advised
#: ``wd discover`` unable to clear it. ``weld._stale_reasons`` says the
#: basis-less states "stay distinguished by the existing top-level ``reason``
#: / ``graph_sha`` / ``commits_behind`` fields"; this is the working-tree
#: signal keeping that promise, which nothing did before. Distinct from the
#: ``stale_sources`` vocabulary in that module, which is closed at four
#: strings and answers "which file diverged, and why" -- inventing a fifth
#: there would mean minting a path to blame, which ADR 0141 rules out.
#:
#: Unlike :data:`NO_GRAPH_REASON` this one is actionable by the remedy the
#: stale-gated commands already print: a discovery pass records the inventory
#: whose absence produced it.
UNVOUCHED_SOURCES_REASON = "dirty sources, and no inventory to vouch for them"


def _cap_stale_sources(entries: list[dict]) -> tuple[list[dict], int]:
    """Sort *entries* by path and cap at :data:`MAX_STALE_SOURCES`.

    Sorting first makes the survivors -- and which paths get elided -- a
    deterministic function of content (ADR 0012), independent of whichever
    git or filesystem order produced *entries*. Returns ``(kept, omitted)``;
    *omitted* is always present on the caller's payload so a capped list is
    never silently indistinguishable from a complete one (ADR 0082: never
    silent-truncate).
    """
    ordered = sorted(entries, key=lambda entry: entry["path"])
    if len(ordered) <= MAX_STALE_SOURCES:
        return ordered, 0
    return ordered[:MAX_STALE_SOURCES], len(ordered) - MAX_STALE_SOURCES


def compute_stale_info(graph_path: Path, meta: dict) -> dict:
    """Return the stale-info dict for a loaded graph (ADR 0017).

    Four orthogonal signals:

    - ``source_stale`` (primary): a file in ``meta.discovered_from``
      changed content -- either committed between ``meta.git_sha`` and
      HEAD, or *uncommitted in the working tree* (staged, unstaged, or a
      new untracked file under a tracked prefix). The working-tree
      dimension is what lets an agent mid-edit see its own changes; a
      commit-range-only check would report fresh until the edit landed.
      Agents should gate ``wd discover`` (and auto-refresh, ADR 0051) on
      this. What that dimension compares is the dirty files' *content*
      against ADR 0008's inventory, not their dirtiness (bd 0jay):
      dirtiness is a fact about HEAD that no discovery run can change, so
      latching on it left the graph unclearably stale for as long as an
      edit was held. :mod:`weld._staleness_worktree` owns that reading.
    - ``sha_behind`` (secondary): the recorded SHA is non-null and
      differs from HEAD.
    - ``coverage_stale`` (ADR 0101): a file that discovery would resolve at
      this commit is absent from the graph's own inventory
      (``discovery-state.json``). The two signals above are both scoped to
      files already in ``meta.discovered_from``, so only this one can see a
      module the graph never ingested. Folded into ``source_stale`` so
      existing callers refresh on it; reported separately for diagnosis.
    - Reverted-content (ADR 0017 fourth amendment,
      :mod:`weld._staleness_reverted`): an inventoried file whose *current*
      content disagrees with its recorded hash, checked directly against the
      inventory rather than waiting for a git-dirty entry to point at it
      first. A file edited, discovered, then reverted to its committed
      content clears every signal above at once -- git reports it clean
      again, the commit range is empty, and the file is already in the
      inventory -- while the graph still holds the edited content. This is
      the last signal consulted, and only once the other three have already
      cleared.

    ``stale`` is aliased to ``source_stale`` for back-compat callers.
    Non-git roots keep the legacy ``stale=False`` + ``reason`` shape.

    An additional pair names *which* source(s) tripped ``source_stale`` and
    why (ADR 0017 amendment): ``stale_sources`` is a
    ``[{"path": ..., "reason": ...}]`` list drawn from
    whichever ONE of the four signals above actually flipped the verdict --
    they run in sequence and the first to trip short-circuits the rest, so
    at most one signal ever contributes -- and ``stale_sources_omitted`` is
    the count :func:`_cap_stale_sources` elided beyond
    :data:`MAX_STALE_SOURCES`. Both keys are always present, including on a
    fresh graph (``[]`` / ``0``), so a consumer never has to probe for them.
    Some stale states have no file-level detail to add -- no recorded
    ``git_sha``, unreachable history (``commits_behind == -1``), or the ADR
    0101 "inventory cannot vouch for the graph body" doubt with no specific
    uncovered file -- and those leave ``stale_sources`` empty rather than
    inventing a path; the existing ``reason`` / ``graph_sha`` /
    ``commits_behind`` fields already distinguish them. See
    :mod:`weld._stale_reasons` for the closed four-string vocabulary.

    One state used to leave ``stale_sources`` empty and set *none* of those
    fields either: the working-tree gate condemning a dirty path against an
    inventory that does not exist. That verdict named nothing at all, which
    is what field-eval finding M1 met at a federation root. It now carries
    :data:`UNVOUCHED_SOURCES_REASON` (ADR 0141 D1), so every ``stale: true``
    this function returns points a reader at something -- a path, a missing
    basis, unreachable history, coverage doubt, or the doubt itself.

    A root with **no graph at all** answers ``reason="no graph"`` (bd 0nqy).
    Everything else here is a comparison *against* a recorded basis, and
    with no graph there is nothing to compare: ``sha_behind`` computes
    False and ``commits_behind`` computes the -1 "unknown" sentinel, which
    together read as reassurance about a graph that does not exist. The
    same two values are also what a graph that *does* exist without a
    recorded ``git_sha`` produces, so without this the two states are
    indistinguishable in the payload -- one needs ``wd discover``, the
    other needs only ``wd touch``. The numeric shape is left alone
    deliberately: ``commits_behind == -1`` is the established "unknown"
    sentinel (:func:`weld.warnings.check_freshness` branches on it and
    :mod:`weld._mcp_read` documents it), so this states the missing fact
    additively rather than retyping a field every consumer reads.

    Graph-only commits (tracked issue) are collapsed: when the only commits
    between ``graph_sha`` and HEAD touched nothing but
    ``.weld/graph.json``, ``sha_behind`` is reported False as well. The
    graph is effectively fresh -- reporting drift in that state drives
    users into a touch/commit/touch loop because ``wd touch`` re-stamps
    HEAD, the user commits the graph, and HEAD advances again. By the same
    rule, dirty ``.weld/`` bookkeeping never feeds ``source_stale`` -- the
    working-tree check excludes those paths.
    """
    root = graph_path.parent.parent  # .weld/ -> project root
    if not is_git_repo(root):
        return {
            "stale": False, "source_stale": False, "sha_behind": False,
            "graph_sha": None, "current_sha": None, "commits_behind": 0,
            "coverage_stale": False, "reason": "not a git repo",
            "stale_sources": [], "stale_sources_omitted": 0,
        }
    cur = get_git_sha(root)
    gsha = meta.get("git_sha")
    tracked = meta.get("discovered_from") or []
    # Both halves are required, and each rules out one neighbouring state.
    #
    # No basis in *meta* alone is not enough: that is exactly the Mode B
    # graph this would otherwise mislabel -- a real tracked graph whose
    # ``git_sha`` lives in a gitignored sidecar the clone did not get. It
    # needs a sidecar synthesis, not a rediscover, and the file on disk is
    # what tells the two apart.
    #
    # No file alone is not enough either: a caller that handed us a basis
    # has a graph, whatever is at *graph_path* -- a library caller holding
    # one in memory, or a run whose body went to ``--output`` elsewhere --
    # and that basis is a real answer worth computing. ``Graph`` over a
    # missing file cannot produce one: it synthesizes a default meta
    # carrying ``version`` and ``schema_version`` but no ``git_sha`` and no
    # ``discovered_from``, so emptiness of the *basis* is the signal, not
    # emptiness of the dict.
    #
    # Ordered after the git probe on purpose: a non-git root has no
    # freshness answer to give whether or not a graph is present, and
    # flipping its long-standing ``stale=False`` shape is not this fix.
    if gsha is None and not tracked and not graph_path.is_file():
        return {
            "stale": True, "source_stale": True, "sha_behind": False,
            "graph_sha": None, "current_sha": cur, "commits_behind": -1,
            "coverage_stale": False, "reason": NO_GRAPH_REASON,
            "stale_sources": [], "stale_sources_omitted": 0,
        }
    if gsha is None:
        behind = -1
    elif gsha == cur:
        behind = 0
    else:
        behind = _commits_behind(root, gsha, cur)
    sha_behind = gsha is not None and gsha != cur
    stale_sources: list[dict] = []
    if gsha is None or behind == -1:
        source_stale = True
    elif not sha_behind:
        source_stale = False
    else:
        changed = source_files_changed_since(root, gsha, tracked)
        source_stale = bool(changed)
        if source_stale:
            stale_sources = [
                {"path": p, "reason": CHANGED_SINCE_DISCOVERY} for p in changed
            ]
    # Working-tree dimension: uncommitted edits to a tracked source file
    # are drift the commit-range diff cannot see. Only consult git status
    # when the committed signal has not already flagged the graph -- this
    # keeps the already-stale paths to a single git call and runs the
    # status probe only on the clean-committed branches (where it is the
    # check that catches the agent's own in-flight edits).
    #
    # Dirtiness alone is not the signal (bd 0jay). Whether a tracked path is
    # uncommitted is a fact about HEAD, and ``wd discover`` commits nothing,
    # so latching on it made ``source_stale`` unclearable for as long as an
    # edit was held -- discovery succeeded and freshness still refused, with
    # the error message naming the fix that could not work. What the graph
    # can actually settle is whether it *read* this content, which ADR 0008's
    # inventory records; ``dirty_sources_diverge`` asks it. Renames are turned
    # off so a vacated original arrives as a deletion -- see that function.
    #
    # Ordering is load-bearing: an inventory that cannot speak for the graph
    # on disk at all is caught by ``coverage_stale`` below, so this branch is
    # free to trust the hashes it holds only while that check still runs after
    # it.
    unvouched = False
    if not source_stale:
        dirty = working_tree_dirty_sources(root, tracked, detect_renames=False)
        # The bool gate runs first, unchanged, and still short-circuits on
        # the first divergence it finds -- the clean-path cost stays exactly
        # what it was. Only once it has already committed to True does the
        # full re-scan run, to name every diverging path instead of the
        # first one (cost paid on the already-stale path only).
        if dirty and dirty_sources_diverge(root, dirty):
            source_stale = True
            stale_sources = dirty_sources_diverge_detail(root, dirty)
            # The gate may not condemn on a state its own detail declines to
            # name (ADR 0141 D1). The two answer the same question from the
            # same inventory, so they disagree only where that inventory is
            # missing entirely -- and then the honest report is the doubt
            # itself, on the field ``weld._stale_reasons`` already points a
            # reader at. Asserted at the composer rather than inside either
            # escape, so it holds for whichever one fired and for any added
            # later; the whole defect was one escape quietly acquiring no
            # counterpart in the detail.
            unvouched = not stale_sources
    # Coverage dimension: probed only once the cheaper signals have cleared
    # the graph, so a genuinely stale read pays nothing extra.
    coverage = False
    if not source_stale:
        coverage = coverage_stale(root)
        if coverage:
            source_stale = True
            stale_sources = coverage_stale_detail(root)
    # Reverted-content dimension: the last signal, and the only one that
    # reads the inventory without a git-dirty or commit-range pointer to a
    # file first. Catches an edit that was discovered and then reverted --
    # every signal above reports clean for that shape (bd lhye; ADR 0017
    # fourth amendment). Probed only once every cheaper signal has cleared,
    # so a genuinely stale read never reaches it.
    if not source_stale:
        reverted = reverted_content_stale(root)
        if reverted:
            source_stale = True
            stale_sources = reverted
    # Collapse pure graph-only drift -- the graph tracks its inputs and
    # no advisory is warranted. Only applies when sources are unchanged.
    if sha_behind and not source_stale and gsha is not None:
        if drift_is_graph_only(root, gsha):
            sha_behind = False
    capped_sources, omitted = _cap_stale_sources(stale_sources)
    info = {
        "stale": source_stale, "source_stale": source_stale,
        "sha_behind": sha_behind, "graph_sha": gsha,
        "current_sha": cur, "commits_behind": behind,
        "coverage_stale": coverage,
        "stale_sources": capped_sources, "stale_sources_omitted": omitted,
    }
    if unvouched:
        # Added rather than always present: ``reason`` is absent on every
        # payload that has a basis to give, so no existing shape changes and
        # a consumer that branches on the key still sees only the states that
        # have nothing else to say. ``render_stale`` already prints it.
        info["reason"] = UNVOUCHED_SOURCES_REASON
    return info
