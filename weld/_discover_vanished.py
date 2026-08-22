"""A file that vanished mid-run decided nothing (ADR 0008, bd rt65).

``files_with_no_nodes`` is read as "a strategy looked at this file and
*decided* it yields nothing", and that reading is what exempts the path from
the ADR 0008 per-file repair. A file deleted between the inventory walk and
the strategy's own listing was never looked at: the run hashed it into
``current_hashes`` at the start, and by the time the strategy ran, its
listing simply did not mention it.

Which strategies can see this at all depends on how they list. Strategies
that go through ``walk_glob`` are served the run-start listing from the glob
memo (bd cjij), so a vanished file still reaches their read guard and is
reported as a failure (bd pt38 / bd o642). The ones that re-list inside their
own ``extract`` with ``Path.glob`` or ``Path.iterdir`` -- ``pydantic``,
``fastapi``, ``worker_stage`` -- cannot report what their listing never
named.

So the check lives here, in the orchestration layer, rather than in any
strategy: only this layer holds both halves of the fact. The inventory says
the file belonged to a source, and the strategy's own ``StrategyResult`` says
it never claimed it.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["vanished_since_inventory"]


def vanished_since_inventory(root: Path, candidates: set[str]) -> set[str]:
    """Of *candidates*, the repo-relative paths that no longer exist at *root*.

    Left in ``files_with_no_nodes`` such a path becomes a permanent exemption
    keyed on the path alone: if the file returns byte-identical it is never
    dirty, so the per-file repair never re-runs it, so it stays absent from
    the graph while every freshness signal reads clean -- the bd hch4 harm by
    another road. Recorded as a failure instead, it stays in the repair queue
    for as long as it is still in scope, and drops out by itself once it is
    not: the next run cannot resolve a deleted path, so it leaves
    ``current_files`` and stops being a candidate.

    Over-reporting is the safe direction here, the same way ADR 0101 §4 takes
    it -- a file that vanished *after* a strategy genuinely decided about it
    costs one re-run, while under-reporting costs silent staleness. One
    ``stat`` per candidate, and the candidate set is only the handful of files
    a run produced no node for.
    """
    return {rel for rel in candidates if not (root / rel).is_file()}
