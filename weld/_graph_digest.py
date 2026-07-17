"""Process-local sha256 memo for large graph artifacts (bd aqqa).

A cold ``wd query`` / MCP read hashes ``.weld/graph.json`` (~16 MB on this
repo) twice: once to validate the query-state sidecar envelope
(:mod:`weld._query_sidecar`) and once as the MCP in-process graph-cache key
(:mod:`weld._mcp_read`). Both digests are of the *same* file at the *same*
instant. This module memoizes the digest per ``(absolute-path, mtime_ns,
size)`` so the second (and any later) caller in the same process reuses the
first hash instead of re-streaming the file.

Freshness contract: the memo key is ``(st_mtime_ns, st_size)``, so any write
that changes the file's size *or* mtime -- weld's own atomic rewrite, a ``git
checkout`` of a committed graph, an editor save -- changes the key and forces a
fresh hash. Every normal weld/git/editor write bumps ``st_mtime_ns``, so in
practice the memo never serves a digest for stale bytes; it is a same-process
deduplication, not a cross-invocation cache (each CLI process starts empty).

This is *no practical change* from the always-content-hash the callers
previously did, with one edge traded away: a same-size *in-place* rewrite that
also holds ``st_mtime_ns`` constant (``os.utime`` forgery, ``cp -p``, ``rsync
--times``) would serve a stale digest for the rest of a long-lived process (the
MCP server). No normal content edit does that -- it changes size or bumps mtime
-- so the case is unreachable outside deliberate timestamp forgery. One entry is
kept per path (a new mtime/size overwrites the prior entry), so the memo is
bounded by the number of distinct graph files a process touches -- one, in
practice.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

__all__ = ["file_sha256", "clear_digest_memo"]

# ``path`` -> ``(mtime_ns, size, hexdigest)``. One entry per path; a new
# mtime/size overwrites the prior entry so the memo cannot grow past the set of
# distinct graph files a process reads.
_DIGEST_MEMO: dict[str, tuple[int, int, str]] = {}


def file_sha256(path: Path) -> str | None:
    """Return the sha256 hexdigest of *path*, memoized by (path, mtime, size).

    Returns ``None`` when the file cannot be stat-ed or read. The memo is keyed
    by the file's ``st_mtime_ns`` and ``st_size``: a change to either busts it.
    Every normal write bumps ``st_mtime_ns``, so in practice the returned digest
    always matches the file's current bytes; only a same-size rewrite that also
    forges ``st_mtime_ns`` (``os.utime``, ``cp -p``) could yield a stale digest,
    which no normal tool produces. See the module docstring for the tradeoff.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = os.path.abspath(os.fspath(path))
    cached = _DIGEST_MEMO.get(key)
    if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2]
    digest = _stream_sha256(path)
    if digest is not None:
        _DIGEST_MEMO[key] = (st.st_mtime_ns, st.st_size, digest)
    return digest


def _stream_sha256(path: Path) -> str | None:
    """Stream *path* through sha256 in 1 MiB chunks, or ``None`` on read error."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def clear_digest_memo() -> None:
    """Drop all memoized digests (test seam; safe in long-lived processes)."""
    _DIGEST_MEMO.clear()
