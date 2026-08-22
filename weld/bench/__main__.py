"""Entry point for ``bazel run //weld/bench:bench`` and ``python -m weld.bench``.

Delegates to :mod:`weld.bench.bench_cli`, the module that owns ``wd bench``'s
argv parsing and mode dispatch (no separate parsing layer here).

``python -m weld.bench`` runs this file as ``weld.bench.__main__`` -- the
same ``-m`` exposure ``weld/__main__.py`` guards against. Until
``weld/bench/__init__.py`` was made import-free (ADR 0099, bd 4g0d), the
parent package's own imports resolved against the launch directory before
this module's first line, which made a guard here inert; now that the
parent is clean, the guard below is real.

Two things about this file are deliberate and will look like mistakes:

* the guard call sits *above* the ``weld.bench.bench_cli`` import, because
  that import is the one it protects. Sorting it into the block below would
  silently undo the fix.
* there is no ``from __future__ import annotations``. A future statement has
  to be the first statement in a module, which would place it ahead of the
  guard, and it is a real import at runtime -- an untrusted repository's
  ``__future__.py`` would run. Nothing here is annotated, so nothing is lost.
"""

# `sys` is a builtin module: it is in `sys.modules` before any user code runs
# and is never resolved from `sys.path`, so no repository can answer it and
# its position here is not load-bearing. `weld._launch_path` is stdlib-only
# for the same reason -- see its module docstring.
import sys

from weld._launch_path import guard_module_launch

# `python -m weld.bench` runs the package's `__main__` submodule, so the name
# recorded as `__main__.__spec__.name` is `weld.bench.__main__` -- not
# `weld.bench`. The guard is inert under every other launch form, including
# `bazel run //weld/bench:bench` (a direct script run with no `__spec__`) and
# `import weld.bench.__main__` from a host application.
guard_module_launch("weld.bench.__main__")

from weld.bench.bench_cli import main as _main  # noqa: E402  (guarded: must stay below the guard)

if __name__ == "__main__":  # pragma: no cover - exercised by bazel run
    raise SystemExit(_main(sys.argv[1:]))
