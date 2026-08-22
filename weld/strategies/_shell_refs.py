"""Repo-relative script paths a shell script names in its own body.

Every release tool in this repo looked like an orphan with no runtime caller.
``weld_impact`` on ``tools/release_mcp_handshake.py`` returned risk_level LOW
with 71 transitive dependents and **not one** of them was
``tools/release_smoke_extras.sh`` -- the script that actually runs it on the
release path. Shell -> script invocation is the entire control flow of that
pipeline and none of it was modelled, so "who runs this tool" had to be
answered with grep (bd x5ec).

What this module can honestly read
----------------------------------
A shell *word* that looks like a repo path ending in ``.py`` or ``.sh``. Not
command position: this repo's own motivating case binds the path to a variable
first and invokes the variable three lines later::

    handshake="${SCRIPT_DIR}/release_mcp_handshake.py"
    ...
    "${venv_python}" "${handshake}" "${VENV_DIR}"

A command-position rule would read the *formatting* of an invocation rather
than its grammar and would miss exactly the case the gap was filed against --
the ADR 0105 mistake restated. Tracking the assignment instead would mean a
second Starlark-style evaluator, this time for a language with word splitting
and dynamic scope, to raise the confidence of an edge that is already honestly
labelled.

So the claim is graded, not guessed: edges are emitted at
``confidence: inferred``, the rank :mod:`weld.strategies.validator_targets`
already uses for the same class of evidence -- a path literal is evidence of a
relationship, not proof of one -- and the rank ADR 0103's ``claim_supersedes``
veto keeps from ever overwriting a definite claim.

What it refuses
---------------
- **Comments.** ``#`` opens a comment when it begins a word (the POSIX rule),
  and quoting suspends it. 163 of the 371 path-like words in this repo's 49
  scripts sit in comments; admitting them would make ``invokes`` mean
  "mentions" and inflate every blast radius joining through it.
- **Paths that do not exist in the worktree.** Fixture and temp-root paths
  (``${WORK}/pkg/a.py``, ``src/auth.py``, ``tools/foo.sh``) resolve to nothing
  and yield nothing, which is what keeps a shell *test* from claiming to
  invoke files it fabricates.
- **Anything reaching outside the repo**: absolute paths, ``..`` traversal,
  and symlinks. Discovery runs against arbitrary user repositories.
- **Self-references.** A script names itself in its own usage and error
  strings; a self-edge is noise in every reader.

Bounded like its sibling referrers: :data:`_MAX_LINES` and
:data:`_MAX_REFS` cap the work per file, and the path pattern caps match
length.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Lines read per script. A shell script longer than this is a generated
#: artefact, not a hand-written entry point.
_MAX_LINES = 2000

#: Characters read per script, applied *before* the line cap. Discovery runs
#: against arbitrary user repositories, where a path matching ``*.sh`` may be
#: a multi-gigabyte artefact; a line cap alone still reads the whole file into
#: memory first. Generous enough that no real script is truncated.
_MAX_CHARS = 1_000_000

#: Distinct referents kept per script. A script that names more paths than
#: this is enumerating the tree, and per-file edges to all of it carry no
#: relationship worth recording (the ``lint_repo.py`` lesson in
#: ``validator_targets``).
_MAX_REFS = 64

#: A word that could be a repo-relative path to a script. Anchored on the
#: extension so match length is bounded by the pattern itself.
_PATH_WORD = re.compile(r"[A-Za-z0-9_${}/.+-]{0,200}?[A-Za-z0-9_]\.(?:py|sh)\b")

#: A leading ``${VAR}/`` or ``$VAR/`` segment. Shell scripts locate their
#: siblings through ``${SCRIPT_DIR}``/``${REPO_ROOT}``-style variables, so the
#: prefix is stripped and the remainder is required to name a real file --
#: the existence check, not the variable's value, is what makes this safe.
_VAR_PREFIX = re.compile(r"^(?:\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*)/")

#: Extensions this module claims. Kept explicit so widening it is a decision.
_SCRIPT_SUFFIXES = (".py", ".sh")


def strip_comment(line: str) -> str:
    """Return *line* with any trailing shell comment removed.

    POSIX: ``#`` opens a comment only when it begins a word, and never inside
    quotes. Both halves matter here -- ``"${VENV_DIR}/#tag"`` is not a
    comment, and ``echo hi #see tools/x.sh`` is.
    """
    out: list[str] = []
    quote: str | None = None
    for index, char in enumerate(line):
        if quote is not None:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            out.append(char)
            continue
        if char == "#" and (index == 0 or line[index - 1] in " \t;&|(){}"):
            break
        out.append(char)
    return "".join(out)


def _normalise(word: str) -> str | None:
    """Reduce a matched word to a candidate repo-relative path, or ``None``."""
    candidate = word.strip("\"'")
    previous = None
    while previous != candidate:
        previous = candidate
        candidate = _VAR_PREFIX.sub("", candidate)
        if candidate.startswith("./"):
            candidate = candidate[2:]
    # An unexpanded variable left anywhere else means the path is not
    # statically known. Unknown yields nothing rather than a guess.
    if not candidate or "$" in candidate or "{" in candidate or "}" in candidate:
        return None
    if candidate.startswith("/") or candidate.startswith("~"):
        return None
    if any(part == ".." for part in candidate.split("/")):
        return None
    if not candidate.endswith(_SCRIPT_SUFFIXES):
        return None
    return candidate


def _resolve(root: Path, sibling_dir: str, candidate: str) -> str | None:
    """Return the repo-relative path *candidate* names, or ``None``.

    Two bases, in order: the repo root, then the referring script's own
    directory. The second is what a stripped ``${SCRIPT_DIR}/`` prefix meant,
    and it is a resolution rather than a guess because the file has to be
    there for the path to be returned at all.
    """
    bases = [candidate]
    if sibling_dir and "/" not in candidate:
        bases.append(f"{sibling_dir}/{candidate}")
    for rel in bases:
        target = root / rel
        try:
            if not target.is_file() or target.is_symlink():
                continue
            # A symlinked *parent* escapes the root just as effectively as a
            # symlinked leaf, so containment is checked on the resolved path.
            # ``relative_to`` raises when the target sits outside.
            target.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        return rel
    return None


def _scan(
    root: Path, text: str, sibling_dir: str, self_path: str | None
) -> list[str]:
    """Shared core: repo-relative script paths named in *text*.

    *self_path*, when given, is excluded from the result -- a script never
    counts as invoking itself. Bounds (:data:`_MAX_LINES`, :data:`_MAX_REFS`)
    are shared by every caller so a fragment of shell text is never scanned
    more permissively than a whole script would be.
    """
    found: set[str] = set()
    for line in text.splitlines()[:_MAX_LINES]:
        for match in _PATH_WORD.finditer(strip_comment(line)):
            candidate = _normalise(match.group(0))
            if candidate is None:
                continue
            resolved = _resolve(root, sibling_dir, candidate)
            if resolved is None or resolved == self_path:
                continue
            found.add(resolved)
            if len(found) >= _MAX_REFS:
                return sorted(found)
    return sorted(found)


def script_references(root: Path, rel_path: str) -> list[str]:
    """Return repo-relative script paths named in *rel_path*'s body.

    Sorted and deduplicated so a caller's edge order is a property of the
    tree rather than of read order (ADR 0012 §3). The referring script never
    appears in its own result.
    """
    source = root / rel_path
    try:
        with source.open(encoding="utf-8") as handle:
            text = handle.read(_MAX_CHARS)
    except (OSError, UnicodeDecodeError):
        return []
    sibling_dir = rel_path.rpartition("/")[0]
    return _scan(root, text, sibling_dir, rel_path)


def shell_text_references(
    root: Path, text: str, sibling_dir: str = ""
) -> list[str]:
    """Return repo-relative script paths named in *text*, a shell fragment.

    Same grammar as :func:`script_references` -- same comment rule, same
    safety refusals, same bounds -- for shell text that is not itself a file
    on disk. The motivating caller is a GitHub Actions ``run:`` step embedded
    in a workflow YAML file: it is shell text with the same grammar a
    standalone script has, extension and all, and reusing this module's
    parser is what keeps a `run:` edge and a `tool_script` edge equally
    honest rather than maintaining two evaluators for one grammar (bd lwrh).

    *sibling_dir* anchors ``${SCRIPT_DIR}``-style resolution the way a real
    script's own directory does for :func:`script_references`; the default
    of ``""`` (repo root) is correct for GitHub Actions, whose steps run
    from ``github.workspace`` unless a step sets its own ``working-directory``.
    There is no *self_path* to exclude: text that is not a file cannot name
    itself.
    """
    return _scan(root, text[:_MAX_CHARS], sibling_dir, None)


__all__ = ["script_references", "shell_text_references", "strip_comment"]
