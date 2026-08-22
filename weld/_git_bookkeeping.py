"""The set of ``.weld/`` paths that are weld output, never user source.

ADR 0096 overflow sibling of :mod:`weld._git`, which sits at the
400-line cap. Split out as its own file because the set is *policy*,
not git plumbing: it is the trust boundary that decides whether a
changed path counts as user source drift. :mod:`weld._git` is its only
consumer (``drift_is_graph_only`` and ``_path_is_tracked``), so it
rides the same zero-dependency ``//weld:git`` micro-library. This
module imports nothing on purpose -- keep it that way.

Inclusion rule (bd eqc4): a path belongs here when weld writes it
without being asked, or when weld's managed ``.weld/.gitignore``
templates list it -- a template line is weld declaring "my output, I
can rebuild it". Persisted *user intent* that weld cannot regenerate
(``viz-views.json``, ``telemetry.disabled``) stays out, as does
source-of-truth config such as ``discover.yaml`` whose edits genuinely
do invalidate the graph. ``weld_bookkeeping_paths_contract_test`` pins
every template line to this set, so a new sidecar cannot be added to
the templates without landing here too.
"""

from __future__ import annotations

__all__ = ["WELD_BOOKKEEPING_PATHS"]

# Weld's own bookkeeping files written by ``wd discover`` / ``wd touch``.
# These are never user *source*: they are outputs of discovery and must
# not contribute to ``source_stale`` (tracked issue), even when a broad
# ``discovered_from`` (e.g. ``['./']`` from default ``wd init``) would
# otherwise match them. Keep this set explicit; do not extend it to
# user-visible files.
WELD_BOOKKEEPING_PATHS = frozenset({
    ".weld/graph.json",
    ".weld/discovery-state.json",
    # Persisted query-state cache written alongside graph.json by
    # ``wd discover`` and refreshed on cache misses by ``Graph.load``
    # (ADR 0031). Same trust boundary, same "never user source" rule.
    ".weld/query_state.bin",
    # Keyword-to-file index written by ``wd discover`` and ``wd
    # build-index``. Functionally a sibling of graph.json -- output of
    # discovery, never user source. Without this entry a user who
    # commits .weld/file-index.json alongside graph.json would see
    # ``wd prime`` report spurious source drift and fall into the same
    # touch/commit loop the other bookkeeping entries already prevent.
    ".weld/file-index.json",
    # SQLite sidecar written alongside graph.json by ``wd discover``
    # (ADR 0058). Pure derived index; same trust boundary as graph.json
    # itself. Must be in this set so a commit that includes graph.db
    # alongside a wd-touched graph.json does not trip the source-drift
    # detector and force a spurious rebuild.
    ".weld/graph.db",
    # Volatile-meta sidecar written alongside graph.json by ``wd discover``
    # / ``wd touch`` (ADR 0065): holds the wall-clock ``updated_at`` and the
    # ``git_sha`` that used to live in graph.json. Gitignored by default,
    # but a user (or a test) who commits it alongside graph.json must not
    # see it counted as a changed *source* file -- it is pure weld output,
    # the same trust boundary as graph.json itself.
    ".weld/graph-meta.json",
    # Surface-hash companion to file-index.json written by ``wd discover``
    # (bd 85tb.2): records the SHA of every indexed file so the next refresh
    # re-tokenizes only what changed. Pure weld output, same trust boundary
    # as file-index.json. Without this entry every read self-heal writes a
    # fresh, untracked ``.weld/file-index-state.json`` that the working-tree
    # drift probe counts as source change -- making every repo perpetually
    # ``source_stale`` and defeating the cheap refresh-on-read contract.
    ".weld/file-index-state.json",
    # Per-refresh sidecar log appended by ``auto_refresh_if_stale``
    # (ADR 0051): carries the ``files_changed`` / ``incremental`` metadata
    # the strict telemetry schema rejects. Weld's own output, same trust
    # boundary as the graph. Without this entry the first auto-refresh
    # leaves an untracked ``.weld/auto-refresh.jsonl`` that a broad
    # ``discovered_from`` reads as user source drift, so freshness answers
    # ``source_stale`` forever afterwards -- exactly the failure mode the
    # ``file-index-state.json`` entry above was added for. The gitignore
    # templates alone cannot repair it: they are skip-if-exists and never
    # rewrite an existing checkout.
    ".weld/auto-refresh.jsonl",
    # Advisory flock file created by ``graph_write_lock`` (ADR 0094). It
    # used to appear only when a *mutating* verb ran; ADR 0096 gate 5
    # takes the same lock to seed a fresh worktree, so a plain ``wd
    # query`` can now leave one behind. Same failure shape as the two
    # entries above: an untracked ``.weld/graph.write.lock`` under a broad
    # ``./`` discovered_from reads as source drift, and the repo is then
    # ``source_stale`` on every read forever. The gitignore templates do
    # list it, but they are skip-if-exists and never rewrite the
    # ``.weld/.gitignore`` of a checkout initialized before ADR 0094 --
    # which is exactly the population this entry protects.
    ".weld/graph.write.lock",
    # --- swept in one pass (bd eqc4) -------------------------------------
    # The three entries above were each added after a separate incident.
    # The rest of the family is enumerated here so the class stops
    # recurring one filename at a time. ``telemetry.jsonl`` is the worst of
    # them: ADR 0035 appends to it on *every* ``wd`` command, so a checkout
    # whose ``.weld/.gitignore`` predates the template line never gets a
    # clean read again.
    ".weld/telemetry.jsonl",
    ".weld/graph-previous.json",
    ".weld/workspace-state.json",
    ".weld/workspace.lock",
    ".weld/agent-graph.json",
    ".weld/graph-communities.json",
    ".weld/graph-community-report.md",
    ".weld/graph-community-index.md",
    ".weld/review-state.json",
    # Sentinel written by the default first-run enrichment flow (ADR 0052).
    # Both gitignore templates now list it (bd lt96), so a fresh checkout
    # gets a clean `git status`; it stays in this set too because it
    # remains weld's own output, never user source, and because a checkout
    # seeded before that template line existed still needs the source_stale
    # protection the templates alone cannot give it (skip-if-exists, never
    # rewritten).
    ".weld/.enrichment-prompted",
    # The Mode B merge policy `wd init --track-graphs` writes (ADR 0110). It
    # is meant to be committed, not ignored, so no gitignore template lists
    # it -- but between being written and being committed it sits untracked
    # in `.weld/`, which is precisely the shape the entries above were added
    # for. It is weld output either way, never user source.
    ".weld/.gitattributes",
})
