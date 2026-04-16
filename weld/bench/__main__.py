"""Entry point for ``bazel run //weld/bench:bench``.

Delegates to :mod:`weld.bench.runner` so the binary always reflects the
runner's current ``main`` (no separate argv parsing layer).
"""

from __future__ import annotations

import sys

from weld.bench.runner import main as _main

if __name__ == "__main__":  # pragma: no cover - exercised by bazel run
    raise SystemExit(_main(sys.argv[1:]))
