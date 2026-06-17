"""Per-language trust check for ``wd doctor``.

Factored out of :mod:`weld.doctor` to keep the entry point under the
400-line cap. The check reads ``.weld/graph.json``, computes the
per-language trust metrics shared with ``wd stats`` (see
:mod:`weld._graph_stats_trust`), and emits a ``warn`` for any language
whose unresolved-symbol ratio crosses an absolute floor.

Why an absolute floor (not a regression-vs-history delta)
---------------------------------------------------------
Weld does not persist a historical per-language trust baseline, so there
is nothing to diff "today" against "yesterday". Rather than invent a
baseline file, the check uses a fixed, documented floor: a language whose
unresolved ratio is above :data:`UNRESOLVED_RATIO_FLOOR` is in a
currently-degraded state regardless of trend. This mirrors how the
staleness and strategy checks report a degraded *state* rather than a
*trend*. The floor is a constant operators can reason about, and the same
``warn`` level keeps the exit code at 0 (advisory, never fatal).

A minimum symbol count (:data:`TRUST_MIN_SYMBOLS`) gates the warning so a
language with only a handful of symbols -- where one unresolved symbol
would blow past any ratio -- does not generate noise.
"""

from __future__ import annotations

import json
from pathlib import Path

from weld._graph_stats_trust import compute_per_language_trust

# A language with fewer than this many symbol nodes is too small for the
# ratio to be meaningful, so it is never warned on (one unresolved symbol
# out of three is 33% but says nothing about trust at scale).
TRUST_MIN_SYMBOLS = 20

# Absolute floor: unresolved_symbol_ratio strictly above this triggers a
# warning. 0.35 means "more than a third of this language's symbols could
# not be placed", which is the point at which call-graph and query output
# in that language stop being trustworthy. Tuned against the closed
# dogfood ledger where per-language origin resolution is the dominant
# noise source for the not-yet-promoted languages.
UNRESOLVED_RATIO_FLOOR = 0.35


def check_language_trust(weld_dir: Path, result_cls: type) -> list:
    """Return per-language trust rows for ``wd doctor``.

    *result_cls* is the ``CheckResult`` shape exported by
    :mod:`weld.doctor`; it is passed in to avoid a circular import.

    Emits at most one ``warn`` per language that is both large enough
    (>= :data:`TRUST_MIN_SYMBOLS` symbols) and over the unresolved-ratio
    floor. Returns an empty list when the graph is missing or unreadable
    (already covered by ``_check_graph_json``) and a single ``ok`` row
    when trust data exists but nothing is degraded, so the section is
    visible rather than silently absent.
    """
    path = weld_dir / "graph.json"
    if not path.is_file():
        return []  # already covered by _check_graph_json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []  # already covered by _check_graph_json

    nodes = data.get("nodes")
    edges = data.get("edges")
    if not isinstance(nodes, dict) or not isinstance(edges, list):
        return []
    trust = compute_per_language_trust(nodes, edges)
    if not trust:
        return []

    warned: list = []
    for lang in sorted(trust):
        metrics = trust[lang]
        if metrics["symbols"] < TRUST_MIN_SYMBOLS:
            continue
        ratio = metrics["unresolved_symbol_ratio"]
        if ratio > UNRESOLVED_RATIO_FLOOR:
            pct = round(ratio * 100, 1)
            floor_pct = round(UNRESOLVED_RATIO_FLOOR * 100, 1)
            warned.append(
                result_cls(
                    "warn",
                    f"{lang}: {pct}% of symbols unresolved "
                    f"(floor {floor_pct}%) -- agents should not trust "
                    f"{lang} call/inherit edges until origin resolution "
                    "improves",
                    "Trust",
                )
            )

    if warned:
        return warned
    return [
        result_cls(
            "ok",
            f"per-language unresolved ratio within floor "
            f"({len(trust)} language(s) measured)",
            "Trust",
        )
    ]


__all__ = [
    "check_language_trust",
    "TRUST_MIN_SYMBOLS",
    "UNRESOLVED_RATIO_FLOOR",
]
