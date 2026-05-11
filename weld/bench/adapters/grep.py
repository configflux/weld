"""``grep`` baseline adapter for the public benchmark (ADR 0059).

Reuses :func:`weld.bench.primitives.grep_baseline` so the public bench
and the existing comparative bench produce the same baseline text and
the same tokenization. The only adapter responsibility here is wrapping
that primitive in the :class:`AdapterResult` envelope.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from weld.bench._public_runner import AdapterResult, PublicTask
from weld.bench.primitives import Prompt, count_tokens, grep_baseline


# Files in a grep match chunk are prefixed with `# file: <rel>` -- the grep
# baseline in weld.bench.primitives emits exactly that marker.
_GREP_FILE_MARKER = re.compile(r"^# file: (\S+)$", re.MULTILINE)


def _prompt_for(task: PublicTask) -> Prompt:
    """Adapt a public-bench task into a comparative-bench Prompt."""
    # The grep_baseline primitive cares about ``term`` (used to filter)
    # and ``category`` (only routes the callgraph + symbol case, which
    # the public corpus encodes via ``family``). Map family -> category
    # using the same vocabulary the legacy bench used; this keeps the
    # behaviour aligned with weld_bench_tasks_test.py.
    family_to_category = {
        "navigation": "navigation",
        "dependency": "dependency",
        "callgraph": "callgraph",
        "impact": "dependency",
        "cross_repo": "dependency",
    }
    return Prompt(
        id=task.id,
        prompt=task.prompt,
        category=family_to_category.get(task.family, "navigation"),
        term=task.term,
        symbol=task.symbol,
    )


def run(task: PublicTask, repo_root: Path) -> AdapterResult:
    """Run the grep baseline against ``repo_root`` for ``task``."""
    start = time.perf_counter()
    try:
        text = grep_baseline(_prompt_for(task), repo_root)
        files = _GREP_FILE_MARKER.findall(text)
        tokens = count_tokens(text)
    except Exception as exc:  # pragma: no cover - defensive
        elapsed = (time.perf_counter() - start) * 1000.0
        return AdapterResult(
            status="degraded",
            files=[],
            tokens=0,
            duration_ms=elapsed,
            error=str(exc),
        )
    elapsed = (time.perf_counter() - start) * 1000.0
    return AdapterResult(
        status="ok",
        files=files,
        tokens=tokens,
        duration_ms=elapsed,
        ttft_ms=elapsed,
    )


__all__ = ["run"]
