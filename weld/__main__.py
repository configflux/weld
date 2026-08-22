"""Module entrypoint for ``python -m weld`` compatibility.

``python -m`` prepends the working directory -- for the CLI, the repository
being scanned -- to ``sys.path`` ahead of the standard library, so every
module weld imports on the way up could be answered, and executed, from that
repository. README's trust model says ``--safe`` scans an untrusted
repository without executing code from it; startup imports happen before safe
mode has any say, since safe mode governs strategy loading and network
egress, not the import path. The guard below closes that. See ADR 0099 and
:mod:`weld._launch_path` for the mechanism and its residual.

Two things about this file are deliberate and will look like mistakes:

* the guard call sits *above* the ``weld.cli`` import, because that import is
  the one it protects. Sorting it into the block below would silently undo
  the fix.
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

# `python -m weld` runs the package's `__main__` submodule, so the name
# recorded as `__main__.__spec__.name` is `weld.__main__` -- not `weld`. The
# guard is inert under every other launch form, including `import
# weld.__main__` from a host application.
guard_module_launch("weld.__main__")

from weld.cli import main  # noqa: E402  (guarded: must stay below the guard)

if __name__ == "__main__":
    sys.exit(main())
