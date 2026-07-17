"""Incremental refresh for the ``wd find`` file index (bd 85tb.2).

The full :func:`weld.file_index.build_file_index` re-walks the repo and
re-parses every Python AST on each call -- ~3.7 s of the auto-refresh
budget on this repo. That cost is paid on *every* refresh, even when one
file changed, because the file index has no notion of "what changed".

This module adds that notion. It keeps a best-effort companion file,
``.weld/file-index-state.json``, that records the SHA-256 of every file
in the index surface as of the last write. On the next refresh we:

1. enumerate the index surface (cheap directory walk) and hash it,
2. diff those hashes against the recorded ones,
3. re-tokenize only the files whose content changed (added or modified),
   drop files that disappeared, and carry every unchanged entry over
   verbatim from the prior ``file-index.json``.

Determinism (ADR 0012 §3): the result is byte-identical to a full
``build_file_index`` because (a) ``tokens_for_file`` is the single
tokenizer used by both the full walk and this updater, and (b)
:func:`weld.file_index.save_file_index` re-sorts and re-serializes the
final ``{path: tokens}`` map with ``sort_keys=True`` -- so the output
bytes depend only on the final map's *content*, never on the order in
which entries were produced or patched.

Safety: the state companion is a pure optimization hint. If it is
missing, malformed, schema-mismatched, or does not describe the current
surface, the updater falls back to a full rebuild -- it can never serve
a stale or partial index. The diff is driven by the index's *own*
surface (every ``_is_indexed_file`` path), not by ``discovery-state``'s
narrower discover.yaml source set, so a changed doc that no discovery
source matches is still re-tokenized and its stale tokens are dropped.

Integrity binding: the companion records ``meta.index_sha256``, the
SHA-256 of the ``file-index.json`` it was written against, and the
refresh verifies it against the live index before trusting the
companion's per-path hashes. This closes the "companion says unchanged
but the index is stale" class: if the index is restored, rewritten, or
truncated independently of the companion (so its per-path hashes describe
a *different* index than the one on disk), the binding no longer matches
and the refresh declines to a full rebuild. It is defense-in-depth within
the ``.weld`` write-trust boundary, not authentication -- an actor with
working-tree write access can rewrite both artifacts (or the source
itself), but honest corruption and independent rollback of either file
can no longer surface stale ``wd find`` results.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from weld._git import get_git_sha
from weld.repo_boundary import iter_repo_files
from weld._notice import emit

#: Schema version for ``file-index-state.json``. Bump on any change to
#: the on-disk shape; an older companion is then treated as absent and a
#: full rebuild reseeds it. Version 2 added ``meta.index_sha256`` (the
#: integrity binding to the ``file-index.json`` the companion describes).
STATE_VERSION: int = 2

#: Companion-file name, adjacent to ``file-index.json`` inside ``.weld``.
STATE_FILENAME: str = "file-index-state.json"

#: File name of the index the companion binds to (sibling in ``.weld``).
INDEX_FILENAME: str = "file-index.json"


def _index_sha256(root: Path) -> str | None:
    """SHA-256 of the on-disk ``file-index.json`` bytes, or ``None``.

    This is the artifact the companion's per-path hashes describe. Hashing
    the raw bytes (not a re-serialization) ties the companion to the exact
    index file present on disk: if the index is rewritten, restored, or
    truncated independently of the companion, the recorded binding no
    longer matches and the incremental path is declined.
    """
    try:
        return hashlib.sha256((root / ".weld" / INDEX_FILENAME).read_bytes()).hexdigest()
    except OSError:
        return None


def tokens_from_content(rel_path: str, content: str) -> list[str]:
    """Canonical sorted token list for *rel_path* given its *content*.

    The pure tokenizer with no I/O, so a caller that already holds the
    bytes (and their hash) can tokenize the exact same bytes -- closing
    the read-twice race where a file edited mid-refresh could be hashed
    and tokenized from different contents (bd 85tb.2).
    """
    # Imported lazily to avoid a circular import: ``file_index`` imports
    # this module's tokenizer from inside ``build_file_index``.
    from weld.file_index import (
        _extract_generic_tokens,
        _extract_markdown_tokens,
        _extract_python_tokens,
        _extract_typescript_tokens,
        _extract_yaml_tokens,
        _tokenize_path,
    )

    suffix = Path(rel_path).suffix
    tokens = _tokenize_path(rel_path)
    if suffix == ".py":
        tokens.extend(_extract_python_tokens(content))
    elif suffix in (".ts", ".tsx", ".js", ".jsx"):
        tokens.extend(_extract_typescript_tokens(content))
    elif suffix == ".md":
        tokens.extend(_extract_markdown_tokens(content))
    elif suffix in (".yaml", ".yml"):
        tokens.extend(_extract_yaml_tokens(content))
    else:
        tokens.extend(_extract_generic_tokens(content))
    return sorted(set(tokens))


def tokens_for_file(root: Path, rel_path: str) -> list[str]:
    """Return the canonical sorted token list for one indexed file.

    The single per-file tokenizer shared by the full
    :func:`weld.file_index.build_file_index` walk and this incremental
    updater, so both paths emit byte-identical per-file token lists
    (ADR 0012 §3). Returns an empty list when the file is unreadable;
    callers treat an empty result as "drop this path", matching the full
    walk which only stores files with a non-empty token set.
    """
    try:
        content = (root / rel_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []
    return tokens_from_content(rel_path, content)


def _surface_hashes(root: Path) -> dict[str, str]:
    """Return ``{rel_path: sha256}`` for every file in the index surface.

    The surface is exactly the set the full walk considers: every file
    ``iter_repo_files`` yields that ``_is_indexed_file`` accepts. Files
    that cannot be read are skipped here just as the full walk skips
    them (an unreadable file contributes no tokens and no hash).
    """
    from weld.file_index import _is_indexed_file

    root = root.resolve()
    out: dict[str, str] = {}
    for filepath in iter_repo_files(root):
        if not _is_indexed_file(filepath):
            continue
        try:
            data = filepath.read_bytes()
        except OSError:
            continue
        out[str(filepath.relative_to(root))] = hashlib.sha256(data).hexdigest()
    return out


def _load_state_hashes(root: Path) -> tuple[dict[str, str], str] | None:
    """Load ``(surface_hashes, index_sha256)``, or ``None`` on any miss.

    A miss (absent, unreadable, malformed, wrong version, wrong shape, or
    missing/blank integrity binding) means "no usable hint" -- the caller
    then rebuilds in full. Never raises; this is a best-effort
    optimization companion.

    ``index_sha256`` is the recorded integrity binding: the SHA-256 of the
    ``file-index.json`` this companion was written against. The caller
    verifies it against the live index so a companion can never be paired
    with an index it does not describe (bd hardening of 85tb.2). The value
    is required and must be a non-empty string; a companion lacking it is
    treated as unusable.
    """
    state_path = root / ".weld" / STATE_FILENAME
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return None
    hashes = raw.get("hashes")
    if not isinstance(hashes, dict):
        return None
    # Validate value types so a tampered companion cannot smuggle
    # non-string entries into the diff.
    for key, val in hashes.items():
        if not isinstance(key, str) or not isinstance(val, str):
            return None
    meta = raw.get("meta")
    index_sha = meta.get("index_sha256") if isinstance(meta, dict) else None
    if not isinstance(index_sha, str) or not index_sha:
        return None
    return hashes, index_sha


def _save_state_hashes(root: Path, hashes: dict[str, str]) -> None:
    """Persist the surface hashes atomically next to ``file-index.json``.

    Best-effort: a write failure is swallowed (the next refresh just
    rebuilds in full). Keys are sorted for a stable, diff-friendly file.

    Records ``meta.index_sha256``, the SHA-256 of the ``file-index.json``
    on disk *now*. Both callers write that index immediately before calling
    this, so the recorded binding describes the index this companion
    documents. If the index cannot be read (so no binding can be computed),
    the companion is not written at all -- an unbound companion would be
    rejected on the next read regardless, so writing one only litters
    ``.weld`` with an artifact that forces a rebuild anyway. The invariant
    is therefore: a companion that exists on disk is always bound.
    """
    index_sha = _index_sha256(root)
    if index_sha is None:
        return
    out_path = root / ".weld" / STATE_FILENAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta: dict = {"version": STATE_VERSION, "index_sha256": index_sha}
    git_sha = get_git_sha(root)
    if git_sha is not None:
        meta["git_sha"] = git_sha
    envelope = {"version": STATE_VERSION, "meta": meta, "hashes": dict(hashes)}
    fd, tmp = tempfile.mkstemp(prefix=f"{STATE_FILENAME}.tmp.", dir=str(out_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(envelope, f, indent=2, ensure_ascii=False, sort_keys=True)
            f.write("\n")
        os.replace(tmp, str(out_path))
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _patched_index(
    root: Path,
    prior_index: dict[str, list[str]],
    prior_hashes: dict[str, str],
    current_hashes: dict[str, str],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Build the new index + the authoritative companion hashes.

    *prior_index* is last run's ``{path: tokens}``. *prior_hashes* and
    *current_hashes* are surface hash maps. Unchanged paths carry over
    verbatim; added/modified paths are re-read once and tokenized from
    *those exact bytes*; deleted paths fall away. The result equals a full
    rebuild because membership is driven by *current_hashes* (the live
    surface) and content by the shared tokenizer.

    Returns ``(index, authoritative_hashes)``. For a changed file the
    returned hash is recomputed from the bytes that produced its tokens --
    not the pre-walk hash -- so a file edited between the surface walk and
    this re-read can never leave the companion claiming "unchanged" while
    holding stale tokens (bd 85tb.2 4-eye review). Unchanged files keep
    their pre-walk hash, which already matches their carried-over tokens.
    """
    new_index: dict[str, list[str]] = {}
    authoritative: dict[str, str] = {}
    for path, sha in current_hashes.items():
        prior_sha = prior_hashes.get(path)
        if prior_sha == sha and path in prior_index:
            new_index[path] = prior_index[path]
            authoritative[path] = sha
            continue
        if prior_sha == sha and path not in prior_index:
            # Unchanged and previously produced zero tokens -> still zero.
            authoritative[path] = sha
            continue
        # Added or modified: read once, hash + tokenize the same bytes so
        # the stored hash and tokens are guaranteed consistent.
        try:
            raw = (root / path).read_bytes()
        except OSError:
            # Vanished between walk and read -> drop it from both maps; the
            # next refresh's surface walk will not list it either.
            continue
        authoritative[path] = hashlib.sha256(raw).hexdigest()
        tokens = tokens_from_content(
            path, raw.decode("utf-8", errors="replace"),
        )
        if tokens:
            new_index[path] = tokens
    return new_index, authoritative


def refresh_file_index(root: Path) -> dict[str, list[str]] | None:
    """Refresh ``file-index.json`` incrementally and update the companion.

    Returns the new index dict on success, or ``None`` if the caller
    should fall back to the full :func:`weld.file_index.build_file_index`
    + :func:`weld.file_index.save_file_index` path (no usable companion,
    or any unexpected error). The companion is only trusted when it
    describes a non-empty surface and parses cleanly.
    """
    from weld.file_index import load_file_index, save_file_index

    try:
        loaded = _load_state_hashes(root)
        if loaded is None:
            return None
        prior_hashes, recorded_index_sha = loaded
        live_index_sha = _index_sha256(root)
        if live_index_sha is None:
            # State companion present but the index it describes is gone
            # (manual wipe, partial restore). Trusting the state alone
            # would carry over nothing and serve an empty index -- rebuild.
            return None
        if live_index_sha != recorded_index_sha:
            # Integrity binding broken: the on-disk file-index.json is not
            # the one this companion documents (it was restored, rewritten,
            # truncated, or tampered independently). The companion's per-path
            # hashes therefore cannot be trusted to mark files "unchanged"
            # against this index -- a carry-over could serve stale tokens.
            # Decline so the caller rebuilds in full and reseeds a bound
            # companion (bd hardening of 85tb.2).
            return None
        current_hashes = _surface_hashes(root)
        if not current_hashes:
            # Empty surface: let the full path handle it (it writes an
            # empty index just the same) rather than guess.
            return None
        prior_index = load_file_index(root)
        new_index, authoritative_hashes = _patched_index(
            root, prior_index, prior_hashes, current_hashes,
        )
        save_file_index(root, new_index)
        # Persist the authoritative hashes (changed files hashed from the
        # bytes that produced their tokens) so the next refresh's
        # change-detection can never desync from the stored tokens.
        _save_state_hashes(root, authoritative_hashes)
        return new_index
    except Exception as exc:  # noqa: BLE001 -- best-effort; fall back to full.
        emit(
            f"[weld] notice: incremental file-index refresh fell back to "
            f"full rebuild: {exc}"
        )
        return None


def reindex_full(root: Path) -> dict[str, list[str]]:
    """Full rebuild that also seeds the incremental companion.

    Used by the discovery sidecar persistence so that after a full
    rebuild the companion hashes are written, enabling the *next*
    refresh to take the incremental path. Mirrors the behaviour of
    ``build_file_index`` + ``save_file_index`` and additionally writes
    ``file-index-state.json``.
    """
    from weld.file_index import build_file_index, save_file_index

    index = build_file_index(root)
    save_file_index(root, index)
    # Seed the companion from the same surface the build just walked so
    # the hashes and the index agree. Best-effort.
    _save_state_hashes(root, _surface_hashes(root))
    return index
