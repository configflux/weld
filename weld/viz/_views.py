"""Saved-views storage for the viz server (bd h6z0.17).

This is the visualizer server's *first* write surface, so the security
constraints (ADR 0092) live here next to the tests rather than inside the
HTTP request handler:

* The only file ever written is ``<root>/.weld/viz-views.json``. That path is
  a constant built from the server root; no request field -- least of all the
  view *name* -- contributes to a filesystem path. Names live only inside the
  JSON document.
* Writes are atomic (``tempfile.mkstemp`` in ``.weld`` + ``os.replace``) which
  replaces a symlink at the destination instead of following it, and a
  process-wide lock serializes read-modify-write so concurrent requests cannot
  corrupt or interleave the file.
* Input is validated and bounded: string ``name``/``hash`` only, names are
  stripped/length-capped/control-char-rejected, the ``hash`` is length-capped
  and treated as opaque data (never evaluated or reflected into a path/shell),
  and the number of stored views is capped. A corrupt or missing file reads
  back as an empty list rather than raising.

The server enables this module only behind the opt-in ``--enable-saved-views``
flag; with the flag off the whole ``/api/views`` route answers ``403`` and
none of these functions run.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

# The single file this module is ever allowed to write, relative to ``.weld``.
VIEWS_FILENAME = "viz-views.json"

# Bounds. Names/hashes are small by nature; the caps exist so a crafted client
# cannot balloon the file or smuggle control bytes. ``MAX_HASH_LEN`` comfortably
# covers a fully-populated location.hash for the h6z0.4 schema.
MAX_VIEWS = 100
MAX_NAME_LEN = 120
MAX_HASH_LEN = 4096
# Transport-level body cap for POST /api/views (bytes). 64 KiB is far larger
# than a legitimate {name, hash} payload but small enough to reject abuse.
MAX_BODY_BYTES = 64 * 1024

# Serializes read-modify-write across ThreadingHTTPServer worker threads.
_WRITE_LOCK = threading.Lock()

# 403 body when the opt-in route is reached while disabled. Deliberately honest
# (the endpoint exists but is refused) so a forgotten flag is actionable.
VIEWS_DISABLED_MSG = (
    "saved views are disabled; restart with --enable-saved-views to allow "
    "writing .weld/viz-views.json"
)


class ViewsError(Exception):
    """A validation/limit failure that maps to an HTTP status."""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


def views_dir(root: Path | str) -> Path:
    """Return the fixed ``.weld`` directory that holds the views file."""
    return Path(root) / ".weld"


def views_path(root: Path | str) -> Path:
    """Return the fixed, only-writable path for saved views."""
    return views_dir(root) / VIEWS_FILENAME


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


def _validate_name(name: Any) -> str:
    """Normalize and validate a view name (stored inside JSON, never a path)."""
    if not isinstance(name, str):
        raise ViewsError("view name must be a string")
    cleaned = name.strip()
    if not cleaned:
        raise ViewsError("view name must not be empty")
    if len(cleaned) > MAX_NAME_LEN:
        raise ViewsError(f"view name too long (max {MAX_NAME_LEN} chars)")
    if _has_control_chars(cleaned):
        raise ViewsError("view name must not contain control characters")
    return cleaned


def _validate_hash(view_hash: Any) -> str:
    """Validate an opaque view hash. Empty is allowed (the canonical view)."""
    if not isinstance(view_hash, str):
        raise ViewsError("view hash must be a string")
    if len(view_hash) > MAX_HASH_LEN:
        raise ViewsError(f"view hash too long (max {MAX_HASH_LEN} chars)")
    if _has_control_chars(view_hash):
        raise ViewsError("view hash must not contain control characters")
    return view_hash


def load_views(root: Path | str) -> list[dict[str, str]]:
    """Read saved views, tolerating a missing or corrupt file (returns [])."""
    try:
        raw = views_path(root).read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, OSError):
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, str]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        view_hash = entry.get("hash")
        if not isinstance(name, str) or not isinstance(view_hash, str):
            continue
        if len(name) > MAX_NAME_LEN or len(view_hash) > MAX_HASH_LEN:
            continue
        out.append({"name": name, "hash": view_hash})
        if len(out) >= MAX_VIEWS:
            break
    return out


def _write_views(root: Path | str, views: list[dict[str, str]]) -> None:
    """Atomically persist *views* to the single fixed views file."""
    directory = views_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=".viz-views.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(views, handle, indent=2, sort_keys=True)
            handle.write("\n")
        # os.replace over a symlink replaces the link, not its target, so a
        # planted symlink cannot redirect the write elsewhere.
        os.replace(tmp, str(views_path(root)))
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def save_view(root: Path | str, name: Any, view_hash: Any) -> list[dict[str, str]]:
    """Upsert a ``{name, hash}`` view; return the full list."""
    name = _validate_name(name)
    view_hash = _validate_hash(view_hash)
    with _WRITE_LOCK:
        views = load_views(root)
        for entry in views:
            if entry["name"] == name:
                entry["hash"] = view_hash
                _write_views(root, views)
                return views
        if len(views) >= MAX_VIEWS:
            raise ViewsError(
                f"too many saved views (max {MAX_VIEWS})", HTTPStatus.CONFLICT
            )
        views.append({"name": name, "hash": view_hash})
        _write_views(root, views)
        return views


def delete_view(root: Path | str, name: Any) -> list[dict[str, str]]:
    """Remove a view by name (idempotent); return the remaining list."""
    name = _validate_name(name)
    with _WRITE_LOCK:
        views = load_views(root)
        remaining = [entry for entry in views if entry["name"] != name]
        if len(remaining) != len(views):
            _write_views(root, remaining)
        return remaining


def read_capped_body(content_length: str | None, reader: Any) -> bytes:
    """Read a request body of ``content_length`` bytes, refusing over the cap.

    ``reader`` is the handler's ``rfile.read`` callable; kept as a parameter so
    the byte-cap policy is unit-testable without a live socket.
    """
    try:
        length = int(content_length) if content_length is not None else 0
    except (TypeError, ValueError):
        raise ViewsError("invalid Content-Length header")
    if length < 0:
        raise ViewsError("invalid Content-Length header")
    if length > MAX_BODY_BYTES:
        raise ViewsError(
            f"request body too large (max {MAX_BODY_BYTES} bytes)",
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )
    return reader(length) if length else b""


def parse_view_payload(body: bytes) -> tuple[Any, Any]:
    """Decode a POST body into ``(name, hash)`` without validating types.

    Type/shape validation is deferred to :func:`save_view` so every rejection
    path funnels through one place. Raises :class:`ViewsError` only for
    malformed transport (non-JSON, non-object).
    """
    if not body:
        raise ViewsError("request body must not be empty")
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ViewsError("request body must be valid JSON")
    if not isinstance(data, dict):
        raise ViewsError("request body must be a JSON object")
    return data.get("name"), data.get("hash")


def name_from_query(query: str) -> str:
    """Extract the required ``name`` query parameter for DELETE."""
    values = parse_qs(query).get("name") or []
    if not values:
        raise ViewsError("the 'name' query parameter is required")
    return values[-1]


def list_payload(root: Path | str) -> dict[str, list[dict[str, str]]]:
    """Return the GET /api/views response body."""
    return {"views": load_views(root)}


def handle_write(
    root: Path | str, method: str, query: str, body: bytes
) -> tuple[dict[str, Any], HTTPStatus]:
    """Dispatch a POST/DELETE /api/views request to ``(payload, status)``."""
    try:
        if method == "POST":
            name, view_hash = parse_view_payload(body)
            views = save_view(root, name, view_hash)
        elif method == "DELETE":
            views = delete_view(root, name_from_query(query))
        else:  # pragma: no cover - server only routes POST/DELETE here
            return {"error": "method not allowed"}, HTTPStatus.METHOD_NOT_ALLOWED
    except ViewsError as exc:
        return {"error": str(exc)}, exc.status
    return {"views": views}, HTTPStatus.OK


def views_response(
    enabled: bool,
    root: Path | str,
    method: str,
    query: str,
    content_length: str | None,
    reader: Any,
) -> tuple[dict[str, Any], HTTPStatus]:
    """Resolve any ``/api/views`` request to ``(payload, status)``.

    Single gate for read + write (ADR 0092): when ``enabled`` is false the
    whole route is ``403`` so the feature is inert and the frontend can probe
    ``GET`` to decide whether to show its control. ``reader`` is the handler's
    ``rfile.read``; only consulted for ``POST``.
    """
    if not enabled:
        return {"error": VIEWS_DISABLED_MSG}, HTTPStatus.FORBIDDEN
    if method == "GET":
        return list_payload(root), HTTPStatus.OK
    try:
        body = read_capped_body(content_length, reader) if method == "POST" else b""
    except ViewsError as exc:
        return {"error": str(exc)}, exc.status
    return handle_write(root, method, query, body)
