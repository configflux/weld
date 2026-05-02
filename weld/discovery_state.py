"""Incremental discovery state tracking.

Manages ``.weld/discovery-state.json`` -- a content-hash index that
records which files were processed during the last discovery run and their
SHA-256 hashes.  Used by the discovery orchestrator to skip unchanged files
on subsequent runs.

Design reference: ADR 0008 (docs/adrs/0008-incremental-discovery.md).
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

#: Current state file schema version.  Bump when the on-disk format changes.
STATE_VERSION: int = 1

#: Filename for the discovery state, adjacent to graph.json.
STATE_FILENAME: str = "discovery-state.json"


@dataclass(frozen=True)
class StateDiff:
    """Result of diffing previous state against the current file set."""

    added: set[str] = field(default_factory=set)
    modified: set[str] = field(default_factory=set)
    deleted: set[str] = field(default_factory=set)

    @property
    def dirty(self) -> set[str]:
        """Files that need re-extraction (added + modified)."""
        return self.added | self.modified

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.deleted)


@dataclass
class DiscoveryState:
    """In-memory representation of the discovery state file."""

    version: int = STATE_VERSION
    created_at: str = ""
    files: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "files": dict(self.files),
        }


def compute_hash(path: Path) -> str:
    """Compute SHA-256 content hash for a single file.

    Returns ``"sha256:<hex>"`` string.  Reads in 64 KiB chunks to handle
    large files without excessive memory use.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def load_state(root: Path) -> DiscoveryState | None:
    """Load discovery state from disk.

    Returns ``None`` if the state file is missing, corrupt, or has an
    incompatible schema version.  Callers should fall back to full
    discovery in all three cases.
    """
    state_path = root / ".weld" / STATE_FILENAME
    if not state_path.is_file():
        return None

    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"[weld] warning: corrupt discovery state file, "
            f"falling back to full discovery: {exc}",
            file=sys.stderr,
        )
        return None

    if not isinstance(raw, dict):
        print(
            "[weld] warning: discovery state file is not a JSON object, "
            "falling back to full discovery",
            file=sys.stderr,
        )
        return None

    version = raw.get("version")
    if version != STATE_VERSION:
        print(
            f"[weld] warning: discovery state version mismatch "
            f"(got {version}, expected {STATE_VERSION}), "
            f"falling back to full discovery",
            file=sys.stderr,
        )
        return None

    files = raw.get("files", {})
    if not isinstance(files, dict):
        print(
            "[weld] warning: discovery state 'files' is not a dict, "
            "falling back to full discovery",
            file=sys.stderr,
        )
        return None

    return DiscoveryState(
        version=version,
        created_at=raw.get("created_at", ""),
        files=files,
    )


def save_state(root: Path, state: DiscoveryState) -> None:
    """Write discovery state to disk atomically."""
    state_dir = root / ".weld"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / STATE_FILENAME

    if not state.created_at:
        state.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    tmp_path = state_path.with_suffix(".tmp")
    try:
        tmp_path.write_text(
            json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(state_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def diff_state(
    old_state: DiscoveryState | None,
    current_files: dict[str, str],
) -> StateDiff:
    """Diff previous state against current file hashes.

    Returns StateDiff with ``added``, ``modified``, and ``deleted`` sets.
    """
    if old_state is None:
        return StateDiff(added=set(current_files.keys()))

    old_files = old_state.files
    current_keys = set(current_files.keys())
    old_keys = set(old_files.keys())

    added = current_keys - old_keys
    deleted = old_keys - current_keys
    modified = {
        p for p in current_keys & old_keys
        if current_files[p] != old_files[p]
    }
    return StateDiff(added=added, modified=modified, deleted=deleted)


def build_file_hashes(root: Path, files: list[str]) -> dict[str, str]:
    """Compute content hashes for a list of repo-relative file paths.

    Skips files that cannot be read (e.g. broken symlinks).
    """
    result: dict[str, str] = {}
    for rel_path in files:
        try:
            result[rel_path] = compute_hash(root / rel_path)
        except OSError:
            pass
    return result


# ---------------------------------------------------------------------------
# Graph helpers for incremental merge (ADR 0008 sections 4-5)
# ---------------------------------------------------------------------------

def purge_stale_nodes(
    nodes: dict[str, dict],
    edges: list[dict],
    stale_files: set[str],
) -> tuple[dict[str, dict], list[dict]]:
    """Remove nodes whose ``props.file`` matches any file in *stale_files*.

    Also removes edges referencing removed nodes.  Used before merging
    incremental results so modified/deleted files get a clean slate.
    """
    if not stale_files:
        return nodes, edges

    removed_ids: set[str] = set()
    surviving_nodes: dict[str, dict] = {}
    for nid, node in nodes.items():
        if node.get("props", {}).get("file", "") in stale_files:
            removed_ids.add(nid)
        else:
            surviving_nodes[nid] = node

    if not removed_ids:
        return nodes, edges

    surviving_edges = [
        e for e in edges
        if e["from"] not in removed_ids and e["to"] not in removed_ids
    ]
    return surviving_nodes, surviving_edges


def files_missing_strategy_outputs(
    existing_graph: dict,
    source_file_map: list[list[str]],
) -> set[str]:
    """Audit *existing_graph* for sources whose files have zero nodes.

    Returns the set of repo-relative file paths for which the strategy
    must re-run to repair a graph that was written without the nodes the
    source was supposed to produce. Each source entry in
    ``source_file_map`` contributes either *all* of its files (when no
    node in the graph has ``props.file`` inside that set) or none.

    Background: the incremental discovery path (ADR 0008) keys re-runs
    on file content hashes. If the previous run committed
    ``discovery-state.json`` with the current files but committed a
    ``graph.json`` that lacks those files' nodes -- e.g. because of a
    crash, partial write, or a sequence where the state-write path ran
    but the symbol-emitting strategy did not -- the dirty set is empty
    and the bug perpetuates: every subsequent incremental run skips the
    strategy and the symbols never reappear short of deleting state.

    The audit closes that gap. Treating "no nodes for any of a source's
    files" as a re-run trigger is conservative: it never produces a
    false positive when the strategy genuinely emits at least one node
    for at least one file in the set, and it costs at most one pass
    over the graph's nodes.
    """
    nodes = existing_graph.get("nodes", {})
    # Different strategies record the source file under different prop
    # keys (``file`` for most, ``declared_in`` for the events family).
    # Treat a file as "has nodes" if any node references it under
    # either key so the audit does not force a perpetual re-run for
    # those strategies.
    files_with_nodes: set[str] = set()
    for node in nodes.values():
        props = node.get("props", {})
        f = props.get("file") or props.get("declared_in")
        if f:
            files_with_nodes.add(f)

    missing: set[str] = set()
    for files in source_file_map:
        if not files:
            continue
        file_set = set(files)
        if not file_set & files_with_nodes:
            missing |= file_set
    return missing


def resolve_source_files(
    root: Path,
    source: dict,
    filter_fn,
) -> list[str]:
    """Resolve files matched by a source entry's glob or files key.

    Returns repo-relative paths.  *filter_fn* is
    ``filter_glob_results`` from the strategies helpers module -- passed
    in to avoid a circular import. The source-level ``exclude`` list is
    applied here so that every entry under ``.weld/discover.yaml`` honours
    excludes uniformly, independent of whether the dispatched strategy
    opts into its own per-file check.
    """
    from weld.glob_match import matches_exclude, walk_glob

    excludes = [p for p in (source.get("exclude") or []) if p]
    files: list[str] = []

    glob_pattern = source.get("glob")
    if glob_pattern:
        matched = walk_glob(root, glob_pattern, excludes=excludes)
        files = [str(p.relative_to(root)) for p in matched]

    path_entry = source.get("path")
    if path_entry and (root / path_entry).exists():
        rel = str((root / path_entry).relative_to(root))
        if not excludes or not matches_exclude(Path(rel).as_posix(), excludes):
            files.append(rel)

    for f in source.get("files", []):
        if not (root / f).exists():
            continue
        rel = str((root / f).relative_to(root))
        if excludes and matches_exclude(Path(rel).as_posix(), excludes):
            continue
        files.append(rel)

    return files
