"""Git-shelling helpers for ``wd impact`` seed resolution.

Extracted from :mod:`weld.impact_cli` to keep that module within the 400-line
cap. Owns the ``--from-diff`` / ``--working-tree`` subprocess plumbing and the
git-repo guard. :mod:`weld.impact_cli` re-exports these names so existing
``from weld.impact_cli import _git_diff_files`` imports keep working.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from weld._git import is_git_repo

__all__ = [
    "_require_git_repo",
    "_git_diff_files",
    "_git_status_files",
    "_reject_dash_ref",
]


def _require_git_repo(root: Path, *, flag: str) -> None:
    """Fail fast with a dedicated message when *root* is not a git repo.

    The ``--from-diff`` and ``--working-tree`` paths shell out to ``git
    diff`` and ``git status``. When *root* is not inside a git working
    tree, those subprocesses leak git's own ``fatal: not a git
    repository`` (or, worse, the multi-page ``--no-index`` usage banner
    on newer git) verbatim into the user-visible error -- functionally
    correct but useless for diagnosing the actual problem. This guard
    detects the condition once, names the offending flag, and points at
    the resolved root so the user knows exactly which directory to fix
    or which ``--root`` to pass instead.
    """
    if is_git_repo(root):
        return
    raise SystemExit(
        f"wd impact: {flag} requires {root} to be a git repository",
    )


def _reject_dash_ref(ref: str) -> None:
    """Reject a ``--from-diff`` ref that git would parse as an option.

    A ``ref`` starting with ``-`` (e.g. ``--upload-pack=evil``) would
    otherwise be parsed by git as an option flag and surface git's
    multi-page ``usage: git diff`` banner -- functionally not RCE
    because the user owns their own CLI invocation, but a confusing
    failure mode for callers and automation. Callers reject the
    leading-dash form up front with a clear weld-prefixed error; the
    git invocation additionally passes ``--end-of-options`` so even a
    future code path that bypasses this check forces git to treat the
    value as a revision rather than a flag.

    Federated fan-out validates the ref once via this guard before any
    per-child git call, so an injection ref is rejected even when no
    child ultimately contributes seeds.
    """
    if ref.startswith("-"):
        raise SystemExit(
            f"wd impact: --from-diff ref cannot start with '-' "
            f"(got: {ref!r}); refs starting with '-' are rejected to "
            f"prevent them from being parsed as git options",
        )


def _git_diff_files(root: Path, ref: str, *, tolerant: bool = False) -> list[str]:
    """Return ``git diff --name-only`` output as a list of paths.

    Accepts ``REF`` (compared against the working tree) or
    ``REF1..REF2``-style ranges -- ``git`` parses both transparently.

    Uses ``-c core.quotePath=false`` and ``-z`` so filenames with non-ASCII
    characters round-trip as UTF-8 instead of git's default C-quoted
    octal-escape form, and so embedded whitespace/newlines in filenames
    cannot collide with the record separator.

    Hardens against argument injection via :func:`_reject_dash_ref` (see
    there) plus ``--end-of-options``.

    *tolerant* (federated fan-out): when the ref does not resolve in *root*
    -- e.g. a SHA that only exists in a sibling child's history -- git exits
    non-zero. With ``tolerant=True`` that child simply contributes no paths
    (return ``[]``) instead of aborting the whole workspace command. A
    missing ``git`` executable is still fatal (it affects every scope).
    """
    _reject_dash_ref(ref)
    cmd = [
        "git", "-c", "core.quotePath=false", "-C", str(root),
        "diff", "--name-only", "-z", "--end-of-options", ref,
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SystemExit("wd impact: 'git' executable not found") from exc
    except subprocess.CalledProcessError as exc:
        if tolerant:
            return []
        stderr = (exc.stderr or "").strip()
        raise SystemExit(
            f"wd impact: git diff failed for '{ref}': "
            f"{stderr or 'no stderr output'}"
        ) from exc
    # ``-z`` emits NUL-separated records; trailing NUL after the last entry
    # yields an empty tail token that the truthy filter drops.
    return [path for path in proc.stdout.split("\0") if path]


def _git_status_files(root: Path) -> list[str]:
    """Return staged + unstaged file paths from ``git status --porcelain=v2 -z``.

    Untracked files (status ``?``) are intentionally included: if the user
    is asking for the working-tree blast radius, brand-new files are part
    of the answer as long as they resolve to graph nodes.

    Uses ``--porcelain=v2 -z`` (NUL-separated, machine-readable) plus
    ``-c core.quotePath=false`` so unicode/quoted filenames round-trip as
    UTF-8 instead of C-quoted octal escapes, and so a filename that
    happens to contain ``" -> "`` is not misclassified as a rename.
    """
    cmd = [
        "git", "-c", "core.quotePath=false", "-C", str(root),
        "status", "--porcelain=v2", "-z",
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SystemExit("wd impact: 'git' executable not found") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise SystemExit(
            f"wd impact: git status failed: {stderr or 'no stderr output'}",
        ) from exc

    # NUL-separated records; trailing NUL after last record yields an empty
    # tail token that we drop with the truthy filter below. Record types
    # (porcelain v2): ``1`` ordinary changed entry, ``2`` rename/copy
    # (followed by an extra NUL-separated original-path token we discard),
    # ``u`` unmerged, ``?`` untracked, ``!`` ignored. Header lines start
    # with ``#`` and are skipped.
    tokens = [tok for tok in proc.stdout.split("\0") if tok]
    paths: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if tok[0] == "#":
            continue
        prefix = tok[0]
        if prefix == "1":
            # ``1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>`` -- 9 fields.
            parts = tok.split(" ", 8)
            if len(parts) == 9:
                paths.append(parts[8])
        elif prefix == "2":
            # ``2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path>``;
            # original-path follows in the next NUL-separated token, which
            # we consume and discard.
            parts = tok.split(" ", 9)
            if len(parts) == 10:
                paths.append(parts[9])
            i += 1
        elif prefix == "u":
            # ``u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>``.
            parts = tok.split(" ", 10)
            if len(parts) == 11:
                paths.append(parts[10])
        elif prefix in ("?", "!"):
            # ``? <path>`` or ``! <path>``.
            parts = tok.split(" ", 1)
            if len(parts) == 2:
                paths.append(parts[1])
    return paths
