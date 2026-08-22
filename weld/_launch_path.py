"""Keep a ``python -m`` entry point from importing its launch directory.

``python -m pkg.mod`` prepends the process working directory to ``sys.path``
-- ahead of the standard library -- and both of weld's ``-m`` entry points
are aimed at a repository from that repository's own directory: MCP clients
start the stdio server there, and ``python -m weld`` is run there. Every
top-level module the process then imports can be answered by a file in the
repository under analysis, and importing a module executes it. A repository
holding its own ``json.py``, ``sqlite3.py``, or ``mcp/`` package gets its
code run by the mere act of pointing weld at it.

The console-script surface (``wd``) never had this exposure: a script's
``sys.path[0]`` is the script's own directory. So this module does not add a
rule, it restores the one the primary surface already follows.

**Importing this module applies the guard for** :data:`GUARDED_MAIN`, which
is the whole point at that entry: the entry has to be gone before the
importing module's *own* imports resolve, and only an import statement can
run that early without displacing the import block below it. Entry points
that are not :data:`GUARDED_MAIN` call :func:`guard_module_launch` with their
own name instead -- ``weld/__main__.py`` does, being a package ``__main__``
rather than a module target.

Either way the guard is inert unless the running ``__main__`` is the module
it was asked about, so library and test imports of anything in this package
leave ``sys.path`` untouched.

Stdlib-only and import-cheap on purpose: this runs before everything else.
That includes having **no** ``from __future__ import annotations``. A future
statement must be the first statement in a module, and it is a real import at
runtime -- so in the first module a guarded entry point imports, it would run
the launch directory's ``__future__.py`` before the guard below could remove
the entry. The annotations here are written to evaluate as-is instead
(``requires-python >= 3.10``). A guarded entry module needs the same
treatment for the same reason, and both of weld's do: ``weld/mcp_server.py``
and ``weld/__main__.py`` each carry no future statement and say so at their
top.

**The removal is early, not first.** It happens before any weld code that
matters, but not before the interpreter: CPython's ``runpy`` bootstrap
imports a handful of modules before the target module's first line -- eight
on CPython 3.12, ``collections`` and ``threading`` and ``warnings`` among
them, with the set varying by version -- so a launch directory holding files
by those names still has them executed. That floor is what every ``-m``
target pays, weld's or the standard library's, and because its membership
moves with the interpreter the tests measure it -- an empty package launched
the same way -- rather than hard-coding a list. Only a launch form that never
adds the entry -- the ``wd`` console script, or ``PYTHONSAFEPATH=1`` --
reaches zero.
"""

import os
import sys

#: The entry point guarded by importing this module, and the default for
#: :func:`guard_module_launch`. Other entry points pass their own name.
#: ``python -m`` records the module it was asked to run as
#: ``__main__.__spec__.name``, which is what separates "we are that process"
#: from "somebody imported us".
GUARDED_MAIN = "weld.mcp_server"

#: Spellings ``python -m`` has used for the launch directory. Python 3.11
#: made it the absolute working directory; older versions inserted the empty
#: string, which the import system resolves against the working directory at
#: each import. Both mean the same place and both are dropped.
_RELATIVE_SPELLINGS = ("", os.curdir)


def is_launch_directory(entry: str, cwd: str) -> bool:
    """Return True when *entry* is the ``python -m`` entry for *cwd*.

    Compared by identity on disk where possible, so a working directory
    reached through a symlink is still recognised; falls back to normalised
    string comparison when the entry cannot be stat'ed.
    """
    if entry in _RELATIVE_SPELLINGS:
        return True
    try:
        return os.path.samefile(entry, cwd)
    except OSError:
        return os.path.abspath(entry) == os.path.abspath(cwd)


def drop_launch_directory(path: list[str], cwd: str) -> str | None:
    """Remove the launch-directory entry from *path*; return what was removed.

    Only index 0 is considered, because that is the one and only entry
    ``python -m`` inserts. Later duplicates are left alone: a ``PYTHONPATH``
    naming the same directory is the user's own declaration about their own
    environment, and quietly editing it would be a second surprise on top of
    the one being removed.

    Returns ``None`` when there is nothing to remove -- an interpreter
    started with ``-P`` or ``PYTHONSAFEPATH=1`` never got the entry, and a
    second call has nothing left to do.
    """
    if not path or not is_launch_directory(path[0], cwd):
        return None
    return path.pop(0)


def guard_module_launch(module_name: str = GUARDED_MAIN) -> str | None:
    """Drop the launch directory iff this process is ``python -m <module_name>``.

    Returns the removed entry, or ``None`` when the guard did not apply --
    which is every case except the guarded entry point being run as a
    module. Never raises: a failure to harden ``sys.path`` must not be the
    reason a server refuses to start.
    """
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    if getattr(spec, "name", None) != module_name:
        return None
    try:
        return drop_launch_directory(sys.path, os.getcwd())
    except Exception:  # noqa: BLE001 -- the promise above is unconditional.
        # Deliberately wider than OSError. `os.getcwd()` raises when the
        # working directory has been unlinked, but sys.path is a plain list
        # anyone may have put anything in, and a stray entry that makes
        # samefile raise ValueError or TypeError must not take the server
        # down on the way up. Not hardening is a worse outcome than
        # crashing only in the sense that it is quiet -- crashing is worse
        # in every other sense.
        return None


# Applied at import time. See the module docstring: an import statement is
# the only thing that can run before the importing module's own imports
# without pushing them below a statement.
guard_module_launch()
