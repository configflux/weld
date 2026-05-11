"""Auto-refresh stale graphs before serving read commands (ADR 0051).

Read commands (``query``, ``find``, ``context``, ``path``, ``brief``,
``impact``, ``callers``, ``references``, ``trace``, ``export``) call
:func:`auto_refresh_if_stale` immediately before loading the graph.
When ``wd stale`` reports ``stale=True``, this helper runs incremental
discovery (per ADR 0008) so the read serves fresh data.

Opt-outs (resolved top-wins):

- ``--no-refresh`` flag bypasses refresh and emits a stderr warning.
- ``WELD_AUTO_REFRESH=0`` env var globally opts out (silent; CI).
- ``--safe`` precedence: refresh still runs, but in safe mode (ADR 0024)
  with banners suppressed.
- Federated roots (``.weld/workspaces.yaml`` present) are skipped --
  ADR 0051 scopes auto-refresh to single-repo today.
- Missing ``.weld/graph.json``: skipped (the missing-graph guard in
  :mod:`weld._graph_cli` handles first-run guidance).

Each successful refresh appends one ``auto-refresh`` telemetry event to
``.weld/telemetry.jsonl`` (ADR 0035) with the source-files-changed count,
elapsed milliseconds, and incremental/full flag.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Final


_ENV_VAR: Final[str] = "WELD_AUTO_REFRESH"
_ENV_OFF_VALUES: Final[frozenset[str]] = frozenset(
    {"0", "off", "false", "no", "disabled"}
)


def _env_disabled(env: Mapping[str, str]) -> bool:
    raw = env.get(_ENV_VAR)
    if raw is None:
        return False
    return raw.strip().lower() in _ENV_OFF_VALUES


def _read_graph_meta(graph_path: Path) -> dict | None:
    """Best-effort read of ``meta`` from ``.weld/graph.json``.

    Returns ``None`` when the file is missing or unreadable. A corrupt
    graph is treated like a missing one -- we cannot make a sensible
    staleness call on it, so the auto-refresh path bails and lets the
    normal load surface a friendly error.
    """
    try:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data.get("meta") if isinstance(data, dict) else None


def _is_federated_root(root: Path) -> bool:
    return (root / ".weld" / "workspaces.yaml").is_file()


def _emit_banner(
    *,
    stderr: IO[str],
    files_changed: int,
    elapsed_ms: int,
    incremental: bool,
    json_output: bool,
    safe: bool,
) -> None:
    """Write a one-line refresh notice unless suppressed.

    Suppressed under ``--json`` (ADR 0040 governs human vs JSON output)
    or ``--safe`` (refresh runs silently per ADR 0051). Suppressed when
    no source files actually changed -- a no-op incremental should not
    nag.
    """
    if json_output or safe or files_changed == 0:
        return
    mode = "incremental" if incremental else "full"
    stderr.write(
        f"[weld] auto-refresh: {files_changed} file(s) changed, "
        f"{mode} refresh in {elapsed_ms} ms\n"
    )


def _emit_no_refresh_warning(stderr: IO[str]) -> None:
    stderr.write(
        "[weld] warning: graph is stale; --no-refresh in effect, "
        "answer may not reflect current source\n"
    )


def _record_telemetry_event(
    root: Path,
    *,
    files_changed: int,
    elapsed_ms: int,
    incremental: bool,
) -> None:
    """Append an ``auto-refresh`` event to ``.weld/telemetry.jsonl``.

    Per ADR 0035 the writer is failure-isolated: if telemetry is
    disabled or the writer raises, the host command keeps its own
    exit code and output. We do *not* go through the
    :class:`Recorder` because this event is not a CLI invocation but a
    side-effect inside one; the parent CLI invocation already records
    its own event.

    The event uses the standard ADR 0035 schema (``duration_ms`` carries
    the refresh wall-clock). The ``files_changed`` and incremental flag
    are surfaced via the stderr banner and via the auto-refresh
    sidecar log (``.weld/auto-refresh.jsonl``) instead of the strictly
    typed telemetry stream so the schema additive-only rule from
    ADR 0035 stays intact.
    """
    try:
        from weld._telemetry import (
            TELEMETRY_SCHEMA_VERSION,
            _python_version_string,
            _utc_now_iso,
            _weld_version_string,
            _write_locked,
            is_enabled,
            resolve_path,
        )
        from weld._telemetry_redact import validate_event

        enabled, _src = is_enabled(cli_flag=None, root=root)
        if not enabled:
            # Telemetry off => no records of any kind. The sidecar is
            # auto-refresh-specific but lives in the same trust
            # boundary, so opt-out applies to it as well.
            return
        path = resolve_path(root)
        if path is None:
            return
        event = {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "ts": _utc_now_iso(),
            "weld_version": _weld_version_string(),
            "surface": "cli",
            "command": "auto-refresh",
            "outcome": "ok",
            "exit_code": 0,
            "duration_ms": int(elapsed_ms),
            "error_kind": None,
            "python_version": _python_version_string(),
            "platform": str(sys.platform) if sys.platform else "unknown",
            "flags": [],
        }
        validated = validate_event(event)
        if validated is not None:
            _write_locked(path, validated)
        # Sidecar carries the ADR 0051 metadata (files_changed,
        # incremental) that the strict telemetry schema rejects. The
        # main stream still records that auto-refresh fired with its
        # duration so dashboards can correlate.
        _record_sidecar_event(
            root,
            files_changed=files_changed,
            elapsed_ms=elapsed_ms,
            incremental=incremental,
        )
    except BaseException:  # noqa: BLE001 -- ADR 0035 failure isolation.
        return


def _record_sidecar_event(
    root: Path,
    *,
    files_changed: int,
    elapsed_ms: int,
    incremental: bool,
) -> None:
    """Append a JSON line to ``.weld/auto-refresh.jsonl`` (best-effort).

    The sidecar stream is the home for per-refresh metadata that does
    not fit the strict ADR 0035 telemetry schema. Bounded to the trailing
    1 MiB / 500 records by the same lazy rotation rule as the main
    stream so it cannot grow unbounded.
    """
    try:
        from weld._telemetry import _utc_now_iso, _write_locked

        weld_dir = root / ".weld"
        if not weld_dir.is_dir():
            return
        sidecar = weld_dir / "auto-refresh.jsonl"
        record = {
            "ts": _utc_now_iso(),
            "command": "auto-refresh",
            "files_changed": int(files_changed),
            "elapsed_ms": int(elapsed_ms),
            "incremental": bool(incremental),
        }
        _write_locked(sidecar, record)
    except BaseException:  # noqa: BLE001 -- failure-isolated per ADR 0035.
        return


def auto_refresh_if_stale(
    root: Path,
    *,
    no_refresh: bool = False,
    safe: bool = False,
    json_output: bool = False,
    env: Mapping[str, str] | None = None,
    stderr: IO[str] | None = None,
) -> dict | None:
    """Run incremental discovery when the graph at *root* is stale.

    Returns a dict ``{"refreshed", "incremental", "elapsed_ms",
    "files_changed"}`` when a refresh ran, or ``None`` when refresh
    was skipped (already fresh, opt-out, missing graph, or federated
    root).

    Errors during the refresh are swallowed: read commands must keep
    serving even when the auto-refresh path itself is broken. The
    fallback in that case is the existing stale graph; the caller's
    next ``wd discover`` will surface the underlying cause.
    """
    env_map = env if env is not None else os.environ
    err = stderr if stderr is not None else sys.stderr

    if _env_disabled(env_map):
        return None

    graph_path = root / ".weld" / "graph.json"
    if not graph_path.is_file():
        return None

    if _is_federated_root(root):
        return None

    # Skip refresh when there is no discover.yaml: the caller seeded a
    # graph by hand (synthetic-fixture tests do this) and there is
    # nothing meaningful to incrementally re-derive. Also keeps unit
    # tests that pre-populate ``.weld/graph.json`` independent of the
    # discovery pipeline.
    if not (root / ".weld" / "discover.yaml").is_file():
        return None

    meta = _read_graph_meta(graph_path)
    if meta is None:
        return None

    # Late import keeps the read-path dependency graph minimal -- the
    # staleness module already encodes the ADR 0017 decision tree.
    from weld._staleness import compute_stale_info

    info = compute_stale_info(graph_path, meta)
    if not info.get("stale"):
        return None

    if no_refresh:
        _emit_no_refresh_warning(err)
        return None

    return _do_refresh(
        root, safe=safe, json_output=json_output, stderr=err,
    )


def _do_refresh(
    root: Path,
    *,
    safe: bool,
    json_output: bool,
    stderr: IO[str],
) -> dict | None:
    """Run the discovery pass and emit telemetry / banner for the result."""
    # Late import: avoid pulling the discovery pipeline (and its
    # transitive deps) into every CLI invocation that does not need it.
    try:
        from weld.discover import _discover_single_repo
        from weld.discovery_state import load_state
        from weld.serializer import dumps_graph
        from weld.workspace_state import atomic_write_text
    except Exception:
        # Discovery module failed to import for whatever reason --
        # better to serve stale than crash the read.
        return None

    started = time.monotonic()
    pre_state = load_state(root)
    pre_hashes = dict(pre_state.files) if pre_state is not None else {}
    incremental = pre_state is not None
    try:
        graph = _discover_single_repo(
            root, incremental=incremental, safe=safe,
        )
        # ``_discover_single_repo`` builds the graph and updates
        # discovery-state, but the persisted ``graph.json`` is only
        # rewritten by the standalone ``wd discover`` CLI. Auto-refresh
        # is a side-effect inside a read command, so we own the write
        # here. ``atomic_write_text`` matches ADR 0011 §8 / 0012 §3.
        graph_path = root / ".weld" / "graph.json"
        atomic_write_text(graph_path, dumps_graph(graph))
    except Exception:
        # Refresh failed -- keep serving stale and let the user see
        # the failure on the next explicit ``wd discover``.
        return None
    elapsed_ms = int((time.monotonic() - started) * 1000)

    post_state = load_state(root)
    post_hashes = dict(post_state.files) if post_state is not None else {}
    files_changed = _count_files_changed(pre_hashes, post_hashes)

    _emit_banner(
        stderr=stderr,
        files_changed=files_changed,
        elapsed_ms=elapsed_ms,
        incremental=incremental,
        json_output=json_output,
        safe=safe,
    )
    _record_telemetry_event(
        root,
        files_changed=files_changed,
        elapsed_ms=elapsed_ms,
        incremental=incremental,
    )
    return {
        "refreshed": True,
        "incremental": incremental,
        "elapsed_ms": elapsed_ms,
        "files_changed": files_changed,
    }


def _count_files_changed(
    pre_hashes: Mapping[str, str], post_hashes: Mapping[str, str],
) -> int:
    """Count files whose discovery-state hash differs across the refresh.

    Files only present in *pre_hashes* count as deletions; files only in
    *post_hashes* count as additions; files in both with different
    hashes count as modifications. The metric is the union -- every file
    that the discovery pass had to react to.
    """
    pre_keys = set(pre_hashes.keys())
    post_keys = set(post_hashes.keys())
    added = post_keys - pre_keys
    deleted = pre_keys - post_keys
    modified = {
        k for k in pre_keys & post_keys if pre_hashes[k] != post_hashes[k]
    }
    return len(added) + len(deleted) + len(modified)


def strip_no_refresh(args: list[str]) -> tuple[list[str], bool]:
    """Remove ``--no-refresh`` from *args*; return ``(stripped, seen)``.

    Used by command parsers that want to expose ``--no-refresh`` without
    threading it through every argparse subparser. Mirrors the shape of
    :func:`weld.cli._strip_no_telemetry`.
    """
    out = [t for t in args if t != "--no-refresh"]
    return out, len(out) != len(args)
