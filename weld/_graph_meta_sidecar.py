"""Volatile-meta sidecar for ``graph.json`` (ADR 0065).

``.weld/graph.json`` is content-addressable except for two volatile meta
fields that change independently of source content:

* ``meta.updated_at`` -- an ISO-8601 wall-clock timestamp (every run).
* ``meta.git_sha`` -- the current ``HEAD`` SHA (every commit; absent
  outside a git checkout).

ADR 0065 relocates those two fields into a sibling, **gitignored** sidecar
``.weld/graph-meta.json`` so two ``wd discover`` runs at a fixed commit
produce byte-identical ``graph.json`` with **no** exempt field to strip.
``meta.discovered_from`` is content-stable and a staleness input, so it
stays in ``graph.json`` -- only the genuinely volatile fields move.

Write path: :func:`write_graph_with_meta` is the paired writer every
canonical ``graph.json`` emitter funnels through -- it strips the volatile
keys from the serialized graph and atomically writes them to the sidecar.

Read path: :func:`merge_sidecar_meta` overlays the sidecar's volatile keys
back onto the in-graph ``meta`` so every consumer sees them as before.
Backward compatibility (the migration fallback): a graph written by an
older weld carries the volatile keys in-graph and has no sidecar; in that
case the in-graph values are kept. A new graph in a fresh checkout where
the gitignored sidecar was never fetched simply lacks ``git_sha`` -- which
``weld._staleness.compute_stale_info`` already treats as ``source_stale``
(ADR 0017), so the read path safely refreshes and rewrites the sidecar.
"""

from __future__ import annotations

import json
from pathlib import Path

from weld.serializer import dumps_graph as _dumps_graph
from weld.workspace_state import atomic_write_text

__all__ = [
    "VOLATILE_META_KEYS",
    "STALENESS_MIRROR_KEYS",
    "SIDECAR_NAME",
    "SIDECAR_VERSION",
    "sidecar_path_for",
    "split_volatile_meta",
    "read_sidecar_meta",
    "merge_sidecar_meta",
    "read_staleness_meta",
    "read_meta_for_staleness",
    "write_graph_with_meta",
    "load_graph_meta",
]

# Meta fields relocated out of ``graph.json`` into the sidecar (ADR 0065).
# Order is irrelevant for correctness; kept stable for readability.
VOLATILE_META_KEYS: tuple[str, ...] = ("updated_at", "git_sha")

# Staleness-precheck mirror fields (bd aqqa). ``discovered_from`` is the
# content-stable staleness input that stays authoritative in ``graph.json``;
# it is *copied* into the sidecar so the read-path precheck
# (:func:`read_staleness_meta`) can reach it without parsing the multi-MB
# graph. ``graph_size`` / ``graph_mtime_ns`` pin the exact ``graph.json`` this
# mirror was written beside, so the reader can prove the mirror is still
# current before trusting it. All three must be present *and* the stat pair
# must match the live ``graph.json`` for the fast path to engage.
STALENESS_MIRROR_KEYS: tuple[str, ...] = (
    "discovered_from",
    "graph_size",
    "graph_mtime_ns",
)

# Sidecar file name; pairs with ``graph.json`` by location in ``.weld/``.
SIDECAR_NAME: str = "graph-meta.json"

# Sidecar envelope format version, independent of ``meta.schema_version``
# and ``weld.contract.SCHEMA_VERSION``.
SIDECAR_VERSION: int = 1

# The sidecar is only ever written for the canonical artifact basename so
# stdout dumps and non-canonical ``--output`` targets do not sprout one.
_CANONICAL_GRAPH_NAME: str = "graph.json"


def sidecar_path_for(graph_path: Path) -> Path:
    """Return the sidecar path paired with *graph_path* (same directory)."""
    return Path(graph_path).parent / SIDECAR_NAME


def split_volatile_meta(graph: dict) -> tuple[dict, dict]:
    """Split *graph* into ``(graph_for_disk, volatile)`` without mutating it.

    ``graph_for_disk`` is a copy of *graph* with the
    :data:`VOLATILE_META_KEYS` removed from ``meta``. ``volatile`` maps each
    present volatile key to its value (absent keys are simply omitted). The
    input dict is never mutated -- callers may still hold the full in-memory
    graph after this returns.

    Only ``meta`` is rewritten, so the copy is shallow at the top level
    with a fresh ``meta`` dict -- ``nodes`` and ``edges`` are shared by
    reference (read-only here; the serializer never mutates them). On a
    6.5k-node graph this avoids a full ~14 MB deep copy that dominated the
    incremental-refresh cost (bd 85tb.2) while preserving the
    no-mutation contract for the volatile keys this function touches.
    """
    on_disk = dict(graph)
    meta = on_disk.get("meta")
    volatile: dict = {}
    if isinstance(meta, dict):
        new_meta = dict(meta)
        for key in VOLATILE_META_KEYS:
            if key in new_meta:
                volatile[key] = new_meta.pop(key)
        on_disk["meta"] = new_meta
    return on_disk, volatile


def _read_sidecar_raw(graph_path: Path) -> dict | None:
    """Best-effort parse of the sidecar into its raw dict, or ``None``.

    Returns ``None`` when the sidecar is missing, unreadable, malformed, or not
    a JSON object -- the normal cases (legacy graph, fresh checkout) that must
    never raise into a read command. Shared by every sidecar reader so the
    parse rules live in one place.
    """
    path = sidecar_path_for(graph_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_sidecar_meta(graph_path: Path) -> dict:
    """Best-effort read of the volatile keys from the sidecar.

    Returns a dict containing only the recognised :data:`VOLATILE_META_KEYS`
    that are present in the sidecar. Returns ``{}`` when the sidecar is
    missing, unreadable, malformed, or shaped unexpectedly -- a missing
    sidecar is the normal case for legacy graphs and fresh checkouts, and
    must never raise into a read command.
    """
    data = _read_sidecar_raw(graph_path)
    if data is None:
        return {}
    return {k: data[k] for k in VOLATILE_META_KEYS if k in data}


def read_staleness_meta(graph_path: Path) -> dict | None:
    """Return ``{git_sha, discovered_from}`` from the sidecar, or ``None``.

    The cheap read-path staleness precheck (bd aqqa): serves the exact two
    fields :func:`weld._staleness.compute_stale_info` reads, *without* parsing
    the multi-MB ``graph.json``. Returns ``None`` to signal the caller must
    fall back to the authoritative full parse.

    Precedence / when fallback triggers -- the sidecar mirror is trusted **only**
    when it both carries every :data:`STALENESS_MIRROR_KEYS` field *and* its
    recorded ``(graph_size, graph_mtime_ns)`` exactly match the live
    ``graph.json`` stat. ``None`` is returned (fall back) for any other state:

    * sidecar missing / unreadable / not an object (legacy graph, fresh
      checkout whose gitignored sidecar was never fetched);
    * sidecar without the mirror (an older or ``wd warm``-stamped sidecar);
    * ``graph.json`` rewritten since the sidecar -- ``wd discover`` (which
      rewrites both together), a ``git checkout`` of a committed graph, or an
      editor save all change ``mtime_ns``, so the stale mirror is rejected.

    When it returns a dict, that dict is byte-identical to what the full parse
    would feed ``compute_stale_info``: ``write_graph_with_meta`` writes
    ``graph.json`` and this mirror in one paired write, and the stat guard
    proves ``graph.json`` has not diverged since. ``git_sha`` may be ``None``
    (non-git root or unavailable git), which is exactly how the full path would
    also see it.
    """
    try:
        graph_stat = Path(graph_path).stat()
    except OSError:
        return None
    data = _read_sidecar_raw(graph_path)
    if data is None:
        return None
    if not all(key in data for key in STALENESS_MIRROR_KEYS):
        return None
    if data.get("graph_size") != graph_stat.st_size:
        return None
    if data.get("graph_mtime_ns") != graph_stat.st_mtime_ns:
        return None
    return {
        "git_sha": data.get("git_sha"),
        "discovered_from": data.get("discovered_from"),
    }


def read_meta_for_staleness(graph_path: Path) -> dict | None:
    """Meta for the staleness precheck: cheap sidecar mirror, else full parse.

    Fast path (bd aqqa): :func:`read_staleness_meta` returns
    ``{git_sha, discovered_from}`` from the sidecar without reading graph bytes
    when the mirror is provably current. Fallback (the prior behaviour):
    parse ``graph.json`` and overlay the sidecar's volatile keys via
    :func:`merge_sidecar_meta`. Both feed ``compute_stale_info`` identical
    ``git_sha`` / ``discovered_from``. Returns ``None`` when ``graph.json`` is
    missing, unreadable, or not a JSON object, so the auto-refresh caller bails
    and lets the normal load surface a friendly error.
    """
    fast = read_staleness_meta(graph_path)
    if fast is not None:
        return fast
    try:
        data = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return merge_sidecar_meta(data.get("meta", {}), graph_path)


def merge_sidecar_meta(graph_meta: dict, graph_path: Path) -> dict:
    """Return *graph_meta* with the sidecar's volatile keys overlaid.

    Resolution per ADR 0065:

    * sidecar value present -> it wins;
    * sidecar value absent but the key is already in *graph_meta* (a graph
      written by an older weld) -> the in-graph value is kept (migration
      fallback);
    * sidecar value absent and not in *graph_meta* -> the key stays absent.

    The input dict is never mutated; a shallow copy is returned.
    """
    merged = dict(graph_meta) if isinstance(graph_meta, dict) else {}
    for key, value in read_sidecar_meta(graph_path).items():
        merged[key] = value
    return merged


def _on_disk_matches(path: Path, candidate: bytes) -> bool:
    """True if ``path`` currently holds exactly *candidate* bytes.

    Compared via sha256 so we never materialize two ~14 MB strings just to
    decide whether a write is needed (bd 85tb.2). A missing or unreadable
    file returns False (write proceeds).
    """
    try:
        if not path.is_file():
            return False
        import hashlib

        existing = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                existing.update(chunk)
        return existing.hexdigest() == hashlib.sha256(candidate).hexdigest()
    except OSError:
        return False


def write_graph_with_meta(
    graph_path: Path,
    graph: dict,
    *,
    on_disk_bytes: bytes | None = None,
    body_matches_disk: bool = False,
) -> None:
    """Atomically write *graph* and its volatile-meta sidecar (ADR 0065).

    The canonical ``graph.json`` is written via the ADR 0012 §3 serializer
    with the volatile keys stripped; those keys are written to
    ``graph-meta.json`` beside it. The sidecar is written only when there
    is a volatile payload **and** *graph_path* is the canonical
    ``graph.json`` basename -- stdout dumps and non-canonical export
    targets get neither a sidecar nor a stripped graph (they round-trip the
    full meta via :func:`weld.serializer.dumps_graph` directly instead).

    *on_disk_bytes* (bd 85tb.2) lets a caller that already serialized the
    *volatile-stripped* graph hand those exact bytes in so this function
    does not re-run ``dumps_graph`` (~900 ms on a 6.5k-node graph). They
    MUST equal ``dumps_graph(split_volatile_meta(graph)[0]).encode()`` --
    i.e. the canonical serialization of the stripped graph. The volatile
    payload for the sidecar is still derived from *graph*.

    *body_matches_disk* (bd 85tb.2): the caller guarantees *on_disk_bytes*
    already equals the current ``graph.json`` body, so the body write is
    skipped outright (no re-hash, no rewrite) and only the volatile sidecar
    is refreshed. Used by the no-change refresh path.

    The input dict is never mutated.
    """
    path = Path(graph_path)
    if path.name != _CANONICAL_GRAPH_NAME:
        # Non-canonical target: preserve legacy single-file behaviour so we
        # never orphan a sidecar next to an arbitrary export path. The
        # stripped-bytes fast path does not apply (full meta round-trips).
        atomic_write_text(path, _dumps_graph(graph))
        return

    on_disk, volatile = split_volatile_meta(graph)
    if on_disk_bytes is not None:
        # bd 85tb.2: when the canonical bytes already match what is on disk
        # (a no-change / content-identical refresh), skip rewriting the
        # multi-MB graph.json body -- only the volatile sidecar below needs
        # refreshing. ``graph.json`` is content-addressable (ADR 0065), so
        # equal bytes means the file is already correct. ``body_matches_disk``
        # lets the caller assert this without paying the re-hash.
        if not (body_matches_disk or _on_disk_matches(path, on_disk_bytes)):
            atomic_write_text(path, on_disk_bytes.decode("utf-8"))
    else:
        atomic_write_text(path, _dumps_graph(on_disk))
    if volatile:
        payload = {"version": SIDECAR_VERSION, **volatile}
        # bd aqqa: mirror the staleness inputs so the read-path precheck can
        # skip parsing graph.json. Pinned to the exact on-disk graph via its
        # (size, mtime_ns); read_staleness_meta rejects the mirror the instant
        # that stat pair stops matching graph.json.
        payload.update(_staleness_mirror(path, graph))
        atomic_write_text(
            sidecar_path_for(path),
            json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        )


def _staleness_mirror(graph_path: Path, graph: dict) -> dict:
    """Return the :data:`STALENESS_MIRROR_KEYS` payload for the sidecar.

    Reads ``discovered_from`` from *graph*'s meta and stats the just-written
    ``graph.json`` to pin the mirror to it. Returns ``{}`` (no mirror; the
    reader falls back to the full parse) when ``discovered_from`` is absent or
    ``graph.json`` cannot be stat-ed -- the mirror is a pure optimisation and
    must never block the paired write.
    """
    meta = graph.get("meta")
    discovered_from = meta.get("discovered_from") if isinstance(meta, dict) else None
    if discovered_from is None:
        return {}
    try:
        graph_stat = graph_path.stat()
    except OSError:
        return {}
    return {
        "discovered_from": discovered_from,
        "graph_size": graph_stat.st_size,
        "graph_mtime_ns": graph_stat.st_mtime_ns,
    }


def load_graph_meta(graph_path: Path) -> dict:
    """Read ``meta`` from *graph_path* with the sidecar overlaid.

    Convenience for the consumers that read ``graph.json`` directly rather
    than through :class:`weld.graph.Graph`. Returns ``{}`` when the graph is
    missing, unreadable, or malformed (the caller's normal load path then
    surfaces a friendly error). The returned ``meta`` already has the
    sidecar's volatile keys merged in per :func:`merge_sidecar_meta`.
    """
    try:
        data = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    meta = data.get("meta")
    return merge_sidecar_meta(meta if isinstance(meta, dict) else {}, graph_path)
