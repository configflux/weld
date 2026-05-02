"""Persisted dismissals for ``wd doctor`` notes.

The doctor emits stable note ids for recommendation-level findings
(missing optional providers, missing MCP config) so the user can record
per-project dismissals in ``.weld/doctor.yaml``. This module owns the
sidecar's load / add / remove path and the ``--ack`` / ``--unack`` /
``--list-acks`` CLI handler.

Security posture: load functions never raise on a malformed sidecar --
doctor must keep working even if the file was hand-edited badly. Writes
go through :func:`weld.workspace_state.atomic_write_text` so callers see
exactly the old bytes or exactly the new.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

from weld._yaml import parse_yaml
from weld.workspace_state import atomic_write_text


_SUPPRESSIONS_FILE = "doctor.yaml"
_SCHEMA_VERSION = 1


# Allow-list of note ids the codebase may emit. ``--ack`` / ``--unack``
# refuse any id not in this set so a typo cannot silently end up in the
# sidecar. Keep this in sync with the ``note_id=`` arguments threaded
# through ``weld.doctor`` and ``weld._doctor_optional``.
VALID_NOTE_IDS: frozenset[str] = frozenset(
    {
        "mcp-config-missing",
        "optional-mcp-missing",
        "optional-anthropic-missing",
        "optional-openai-missing",
        "optional-ollama-missing",
        "optional-copilot-cli-missing",
    }
)


def _path_for(weld_dir: Path) -> Path:
    return weld_dir / _SUPPRESSIONS_FILE


def load_suppressions(weld_dir: Path) -> set[str]:
    """Read suppressed note ids from ``.weld/doctor.yaml``.

    Returns an empty set when the file is missing, empty, or malformed.
    Never raises -- doctor must remain functional even on a corrupt
    sidecar.
    """
    path = _path_for(weld_dir)
    if not path.is_file():
        return set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    if not text.strip():
        return set()
    try:
        data = parse_yaml(text)
    except Exception:  # noqa: BLE001 -- malformed sidecar must not crash doctor
        return set()
    if not isinstance(data, dict):
        return set()
    raw = data.get("suppressed", [])
    if not isinstance(raw, list):
        return set()
    out: set[str] = set()
    for item in raw:
        if isinstance(item, str) and item:
            out.add(item)
    return out


def _emit(weld_dir: Path, ids: set[str]) -> None:
    """Write the sidecar with a deterministic, sorted id list."""
    sorted_ids = sorted(ids)
    lines = [f"version: {_SCHEMA_VERSION}", "suppressed:"]
    if sorted_ids:
        for note_id in sorted_ids:
            lines.append(f"  - {note_id}")
    else:
        # Keep an empty list explicit so the file round-trips cleanly.
        lines[-1] = "suppressed: []"
    text = "\n".join(lines) + "\n"
    atomic_write_text(_path_for(weld_dir), text)


def add_suppression(weld_dir: Path, note_id: str) -> bool:
    """Add ``note_id`` to the sidecar. Return ``True`` if changed."""
    current = load_suppressions(weld_dir)
    if note_id in current:
        return False
    current.add(note_id)
    _emit(weld_dir, current)
    return True


def remove_suppression(weld_dir: Path, note_id: str) -> bool:
    """Remove ``note_id`` from the sidecar. Return ``True`` if changed."""
    current = load_suppressions(weld_dir)
    if note_id not in current:
        return False
    current.discard(note_id)
    _emit(weld_dir, current)
    return True


# ── CLI handler ──────────────────────────────────────────────────────


def _validate_ids(ids: Sequence[str]) -> str | None:
    """Return an error message for the first unknown id, else ``None``."""
    for note_id in ids:
        if note_id not in VALID_NOTE_IDS:
            return f"unknown note id: {note_id}"
    return None


def handle_ack_flags(
    root: Path,
    *,
    ack: Sequence[str] | None,
    unack: Sequence[str] | None,
    list_acks: bool,
) -> int:
    """Process ``--ack`` / ``--unack`` / ``--list-acks`` and return exit code.

    Caller (``weld.doctor.main``) is responsible for the argparse
    machinery; this function only runs when at least one of the flags is
    set. It refuses when ``.weld/`` is not present and rejects unknown
    note ids before touching the filesystem.
    """
    weld_dir = root / ".weld"
    if not weld_dir.is_dir():
        sys.stderr.write("no Weld project here -- run wd init\n")
        return 2

    # Validate all ids up-front so a partial write is not produced.
    for ids in (ack or (), unack or ()):
        err = _validate_ids(ids)
        if err is not None:
            sys.stderr.write(err + "\n")
            return 2

    if list_acks:
        for note_id in sorted(load_suppressions(weld_dir)):
            sys.stdout.write(note_id + "\n")
        return 0

    for note_id in ack or ():
        if add_suppression(weld_dir, note_id):
            sys.stdout.write(f"acknowledged: {note_id}\n")
        else:
            sys.stdout.write(f"already acknowledged: {note_id}\n")
    for note_id in unack or ():
        if remove_suppression(weld_dir, note_id):
            sys.stdout.write(f"cleared: {note_id}\n")
        else:
            sys.stdout.write(f"not acknowledged: {note_id}\n")
    return 0
