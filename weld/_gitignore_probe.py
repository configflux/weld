"""Ask git whether a path is ignored, and name the rule that does it.

``wd init --track-graphs`` promises a repository whose ``graph.json`` gets
committed. Whether that promise holds is not a property of the file weld
writes -- it is a property of git's whole ignore stack, and weld manages
exactly one layer of it. bd ilax closed the case where the *managed*
``.weld/.gitignore`` contradicts the requested mode. This module closes the
layer above: a repository whose **root** ``.gitignore`` carries ``.weld/`` --
the line people write before they learn weld ships its own policy file --
got Mode B declared and none of it applied. Git never saw ``graph.json``,
``discovery-state.json`` or ``file-index.json``, so the clone that was
supposed to arrive warm arrived with nothing, and weld reported success
(bd jya6).

**Ask git rather than parse.** ``git check-ignore`` answers for the entire
stack at once -- the root ``.gitignore``, every ``.gitignore`` on the way
down, ``.git/info/exclude``, the global ``core.excludesFile``, and weld's own
managed file -- with git's real precedence rules, including the one no hand
parser gets right: a negation cannot re-include a file whose *parent
directory* is excluded, so ``.weld/`` followed by ``!.weld/graph.json``
still hides the graph. It also answers for paths that do not exist yet,
which is the case at ``wd init`` time. The cost is one subprocess, once, at
init -- which already shells out to git to register the merge driver.

**The index is deliberately consulted.** Without ``--no-index``, git reports
an already-tracked path as *not* ignored, which is the truth that matters
here: git keeps committing a tracked file whatever the ignore rules say, so
Mode B is in effect for it. ``--no-index`` would answer a different, purely
hypothetical question and would refuse a repository that is already working.

Failure is never fatal. Outside a git checkout, or with no ``git`` on
``PATH``, the verdict is *unanswered* and the caller falls back to the
managed-file predicate (:func:`weld._gitignore_writer.ignore_expresses_mode`)
-- ``wd init`` is supported outside a checkout and must not start failing
there.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple

__all__ = ["IgnoreVerdict", "UNANSWERED", "check_ignore"]

#: How long to wait for ``git check-ignore``. Matches
#: :func:`weld._gitattributes_writer.register_merge_driver`: a git that has
#: not answered in ten seconds is not going to, and init must not hang.
_TIMEOUT_S = 10


class IgnoreVerdict(NamedTuple):
    """What git said about one path.

    *answered* is the only field to test first: ``False`` means git could
    not be asked at all (no checkout, no ``git``, a path outside the
    repository), and the other two fields carry no claim. *ignored* is
    git's verdict; *rule* names the rule responsible, as
    ``<source>:<line>:<pattern>``, and is ``None`` whenever nothing ignores
    the path.
    """

    answered: bool
    ignored: bool
    rule: str | None


#: The "git could not answer" verdict, spelled once so callers compare
#: against a name rather than reconstructing the tuple.
UNANSWERED = IgnoreVerdict(answered=False, ignored=False, rule=None)


def _absolute_source(root: Path, source: str) -> str:
    """*source* as an absolute path when it names a real file under *root*.

    ``git check-ignore`` prints the ignore file relative to the repository
    root (``.gitignore``, ``.weld/.gitignore``, ``.git/info/exclude``), while
    a global ``core.excludesFile`` comes back already absolute. A diagnostic
    that names a bare ``.gitignore`` is ambiguous in a repository with
    several, so relative sources are resolved against *root* -- but only when
    that resolves to a file that exists, so an unexpected spelling is
    reported verbatim rather than turned into a path that is not there.
    """
    if not source:
        return source
    candidate = Path(source)
    if candidate.is_absolute():
        return source
    resolved = root / candidate
    return str(resolved) if resolved.is_file() else source


def check_ignore(root: Path, target: Path) -> IgnoreVerdict:
    """Ask git whether *target* is ignored in the checkout at *root*.

    *target* is passed on stdin with ``-z`` so a path containing a newline,
    a tab, or a leading dash cannot be misread as a second path or as an
    option; ``-v -z`` then answers in the unambiguous
    ``source NUL line NUL pattern NUL path NUL`` form, which is why the
    pattern is free to contain the colons the human-readable format would
    make un-parseable.

    Exit statuses are git's own: ``0`` ignored, ``1`` not ignored, anything
    else an error -- reported as :data:`UNANSWERED` rather than raised,
    together with every ``OSError``/timeout, because a repository weld cannot
    interrogate is not a repository weld should refuse to initialise.
    """
    try:
        completed = subprocess.run(
            ["git", "check-ignore", "-v", "-z", "--stdin"],
            cwd=str(root),
            input=str(target).encode("utf-8") + b"\0",
            capture_output=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return UNANSWERED
    if completed.returncode == 1:
        return IgnoreVerdict(answered=True, ignored=False, rule=None)
    if completed.returncode != 0:
        return UNANSWERED
    fields = completed.stdout.decode("utf-8", errors="replace").split("\0")
    if len(fields) < 3:
        # Ignored, but the verbose record did not arrive in the shape this
        # git documents. The verdict still stands; only the culprit is lost.
        return IgnoreVerdict(answered=True, ignored=True, rule=None)
    source, line, pattern = fields[0], fields[1], fields[2]
    return IgnoreVerdict(
        answered=True,
        ignored=True,
        rule=f"{_absolute_source(root, source)}:{line}:{pattern}",
    )
