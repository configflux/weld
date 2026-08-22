"""Say when ``wd`` is not the weld in the tree you are standing in (bd emmg).

``wd`` is a console script, so it resolves to whatever copy of weld is
installed on ``PATH`` -- never to the checkout you happen to be sitting in.
Inside a weld source checkout those two are different things, and the
difference is invisible: ``wd discover`` runs the *installed* build against
your tree and reports a perfectly plausible result that owes nothing to the
change you just made. A verification pass that concludes "my fix changes
nothing" is then indistinguishable from a correct negative, which is exactly
how it has been read.

Documentation already warned about this (CONTRIBUTING.md tells contributors to
use the ``python -m`` form) and the mistake kept happening, because a rule is
read long before the moment it applies. So weld says it at that moment
instead: one line, on stderr, when and only when the running package is not
the checkout's.

Two design points are load-bearing:

* **The trigger is path identity, not a version difference.** ``VERSION`` is
  bumped at release, so every unversioned change on top of a release --
  i.e. all of development -- would slip past a version comparison while being
  just as unexercised. Versions are carried *in* the message because they name
  the two sides usefully; they do not decide whether it is emitted.
* **Silence is the common case.** The notice needs a weld source checkout
  above the current directory, so ordinary users of an installed weld never
  see it, and an editable install of the checkout you are in resolves to the
  same package directory and stays silent too -- fixing the environment turns
  the notice off by itself.

stdout is never touched: the line goes through :func:`weld._notice.emit`,
which is stderr-only, so a ``--json`` payload stays a clean parse.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final, IO

from weld._notice import emit
from weld._version import weld_version

__all__ = [
    "ENV_VAR",
    "emit_source_checkout_notice",
    "find_source_checkout_root",
]

#: Set to any of :data:`_ENV_OFF_VALUES` to silence the notice. Named the same
#: way as ``WELD_TELEMETRY`` and accepting the same off-words, so one habit
#: covers both.
ENV_VAR: Final[str] = "WELD_SOURCE_CHECKOUT_NOTICE"

_ENV_OFF_VALUES: Final[frozenset[str]] = frozenset(
    {"off", "0", "false", "no", "disabled"}
)

#: Same bound, and the same reasoning, as :mod:`weld._version`: ``VERSION`` is
#: unschema'd text that ends up on a terminal, so anything longer than a
#: version is corruption and is rejected rather than truncated.
_MAX_VERSION_LEN: Final[int] = 128

_UNKNOWN: Final[str] = "unknown"


def _is_source_checkout(candidate: Path) -> bool:
    """Does *candidate* look like a weld source checkout?

    Both markers are required. ``VERSION`` alone is a common file in any
    project; ``weld/_version.py`` beside it is the package layout
    :func:`weld._version.version_file_path` already depends on, so the pair
    identifies weld's own tree and essentially nothing else.
    """
    try:
        return (candidate / "VERSION").is_file() and (
            candidate / "weld" / "_version.py"
        ).is_file()
    except OSError:
        return False


def find_source_checkout_root(start: Path | str) -> Path | None:
    """Nearest self-or-ancestor of *start* that is a weld source checkout.

    Nearest wins, which is what makes worktrees come out right: a worktree
    created inside another checkout is itself a checkout, and it is the one
    the caller is standing in. The walk is lexical (``..`` is normalised, no
    symlink is resolved) and touches only ``is_file``, so nothing here spawns
    a process or reads a file -- this runs before every single ``wd`` command.

    Returns ``None`` when no ancestor qualifies, including when *start* does
    not exist.
    """
    normalised = Path(os.path.abspath(start))
    for candidate in (normalised, *normalised.parents):
        if _is_source_checkout(candidate):
            return candidate
    return None


def _checkout_version(root: Path) -> str:
    """Version recorded by the checkout at *root*, or ``"unknown"``."""
    try:
        with (root / "VERSION").open(encoding="utf-8") as handle:
            text = handle.readline(_MAX_VERSION_LEN + 1).strip()
    except Exception:  # noqa: BLE001 -- a corrupt VERSION raises ValueError
        # subclasses (UnicodeDecodeError) as readily as OSError, and no read
        # failure here is worth more than "unknown".
        return _UNKNOWN
    if not text or len(text) > _MAX_VERSION_LEN:
        return _UNKNOWN
    return text


def _suppressed() -> bool:
    return os.environ.get(ENV_VAR, "").strip().lower() in _ENV_OFF_VALUES


def _same_directory(left: Path, right: Path) -> bool:
    """Do both paths name the same directory once symlinks are followed?

    An editable install is commonly reached through a link, so comparing
    spellings would report a mismatch where there is none.
    """
    try:
        return os.path.realpath(left) == os.path.realpath(right)
    except OSError:
        return False


def emit_source_checkout_notice(
    *,
    cwd: Path | str | None = None,
    package_dir: Path | str | None = None,
    stream: IO[str] | None = None,
    running_version: str | None = None,
) -> bool:
    """Emit the mismatch notice if there is one to emit; return whether it was.

    All four arguments are test seams with production defaults: the current
    working directory, the directory this module lives in (i.e. the ``weld``
    package actually executing), ``sys.stderr`` via :func:`weld._notice.emit`,
    and :func:`weld._version.weld_version`.

    The caller is a startup path, so this must not raise; every branch that
    can fail degrades to "no notice" or to ``"unknown"``.
    """
    if _suppressed():
        return False
    if cwd is None:
        try:
            cwd = Path.cwd()
        except OSError:
            # A deleted working directory is a real state (a worktree removed
            # from under a shell), and "where am I" is then unanswerable.
            return False
    root = find_source_checkout_root(cwd)
    if root is None:
        return False
    running_pkg = (
        Path(__file__).resolve().parent
        if package_dir is None
        else Path(os.path.abspath(package_dir))
    )
    if _same_directory(running_pkg, root / "weld"):
        return False
    emit(
        f"running weld {running_version or weld_version() or _UNKNOWN} from "
        f"{running_pkg} -- not the checkout you are in ({root}, VERSION "
        f"{_checkout_version(root)}), so changes in this tree are not "
        f"exercised; use `python3 -m weld` from the checkout. "
        f"Silence: {ENV_VAR}=off",
        stream=stream,
    )
    return True
