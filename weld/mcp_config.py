"""Per-client MCP config snippet generator (ADR 0023).

Renders the JSON snippet that configures Weld's stdio MCP server in the
three documented clients (Claude Code, VS Code, Cursor). Default behaviour
is to print the snippet -- the tracked issue acceptance bar -- with optional
``--write`` / ``--merge`` / ``--force`` / ``--dry-run`` flags for safer
in-place edits of the client-appropriate file.

The MCP server invocation (``wd mcp serve``) is identical across clients;
only the wrapping JSON shape and target file path differ. This module is the
single source of truth for both pieces. The invocation it renders has to
stay routable by :mod:`weld._mcp_cli`, which owns the ``wd mcp`` subcommand
table -- drift between the two is invisible here and fatal in a client.

Layout intent: keep all three clients in one ~250-line file rather than
splitting per client. The per-client variation is small (one key name,
one path), so a per-client module would mostly duplicate boilerplate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from weld._safe_text import sanitize_json_text

# ---------------------------------------------------------------------------
# Client registry
# ---------------------------------------------------------------------------

# The three supported clients. The "servers_key" differs because VS Code's
# MCP integration uses ``servers`` while Claude Code and Cursor use
# ``mcpServers``. Keep this table small and obvious; new clients should add
# one row, not a new abstraction layer.
@dataclass(frozen=True)
class _ClientSpec:
    name: str
    servers_key: str
    target_path: Path  # relative to repo root


_CLIENTS: dict[str, _ClientSpec] = {
    "claude": _ClientSpec(
        name="claude",
        servers_key="mcpServers",
        target_path=Path(".mcp.json"),
    ),
    "cursor": _ClientSpec(
        name="cursor",
        servers_key="mcpServers",
        target_path=Path(".cursor") / "mcp.json",
    ),
    "vscode": _ClientSpec(
        name="vscode",
        servers_key="servers",
        target_path=Path(".vscode") / "mcp.json",
    ),
}

# The MCP server entry. Same across clients: the `wd` console script.
# Centralised so the generator never drifts from docs/mcp.md.
#
# Console script rather than `python -m weld.mcp_server`, for two reasons.
# A script's sys.path[0] is the script's own directory, so the directory a
# client launches the server in -- the repository being served -- is never on
# the module search path; `python -m` puts it there ahead of the standard
# library, before any weld code can run. And a console script hard-codes its
# interpreter in the shebang, so it cannot be handed an environment whose
# `python` resolves to a different install than the one weld was installed
# into. The `-m` form remains supported for source checkouts, where serving
# the checkout rather than an installed copy is the point.
_SERVER_ENTRY: dict[str, Any] = {
    "command": "wd",
    "args": ["mcp", "serve"],
}

_SERVER_NAME = "weld"


class UnknownClientError(ValueError):
    """Raised when a caller asks for a client name we don't support."""


def supported_clients() -> tuple[str, ...]:
    """Return the supported client names in stable, documented order."""
    return ("claude", "vscode", "cursor")


def _spec_for(client: str) -> _ClientSpec:
    if client not in _CLIENTS:
        names = ", ".join(supported_clients())
        raise UnknownClientError(
            f"unknown MCP client: {client!r}. Supported clients: {names}."
        )
    return _CLIENTS[client]


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render(client: str) -> str:
    """Return the formatted JSON snippet to paste into *client*'s config.

    The output is pretty-printed with two-space indent and a trailing
    newline, matching the existing repo convention in ``.mcp.json``.
    """
    spec = _spec_for(client)
    payload = {spec.servers_key: {_SERVER_NAME: dict(_SERVER_ENTRY)}}
    return json.dumps(payload, indent=2) + "\n"


def target_path(client: str) -> Path:
    """Return the relative target path for *client* (e.g. ``.mcp.json``)."""
    return _spec_for(client).target_path


# ---------------------------------------------------------------------------
# Write / merge
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WriteResult:
    """Result of a :func:`write_config` call.

    ``wrote`` is True iff the on-disk file was modified (or would be, when
    ``dry_run=True`` -- see :attr:`would_write`). ``would_write`` reports
    the in-principle decision regardless of dry-run, so callers can shape
    diagnostic output without inspecting flags themselves.

    ``error`` distinguishes "writer refused for a benign reason" (e.g.
    already up to date, no flag passed) from "writer refused because the
    request could not be honoured safely" (e.g. existing file is not
    parseable JSON in ``--merge`` mode). The CLI maps ``error=True`` to a
    non-zero exit code so scripted users can detect the failure.
    """

    wrote: bool
    would_write: bool
    path: Path
    reason: str = ""
    error: bool = False


def _merge_payload(existing: dict[str, Any], spec: _ClientSpec) -> dict[str, Any]:
    """Return *existing* with our weld server entry merged into ``servers_key``.

    Siblings under the same key are preserved. Any pre-existing ``weld``
    entry is replaced so the merge is idempotent when our payload already
    matches but corrective when it does not.
    """
    merged: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    servers = merged.get(spec.servers_key)
    if not isinstance(servers, dict):
        servers = {}
    servers = dict(servers)
    servers[_SERVER_NAME] = dict(_SERVER_ENTRY)
    merged[spec.servers_key] = servers
    return merged


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* via tmpfile + ``os.replace``.

    Backup of any pre-existing file is the caller's responsibility; this
    helper only guarantees that the final swap is atomic so a crash mid-
    write cannot leave a half-written config behind.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def write_config(
    client: str,
    *,
    root: Path | None = None,
    write: bool = True,
    merge: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> WriteResult:
    """Render the snippet for *client* and (optionally) write it to disk.

    Parameters
    ----------
    client:
        One of :func:`supported_clients`.
    root:
        Repo root the relative target path is resolved against. Defaults
        to the current working directory (matching the rest of the wd CLI
        which also operates on cwd).
    write:
        Whether to actually attempt a write. The CLI passes ``True`` only
        when the user opts in via ``--write``; with ``write=False`` and
        ``merge=False`` the function is a no-op returning ``would_write=False``.
    merge:
        When True, the rendered server entry is merged into an existing
        config file's server map (preserving siblings). When False, the
        rendered snippet is written verbatim, but only if the file is
        absent or ``force`` is set.
    force:
        Allow overwriting an existing file whose content differs from the
        rendered snippet (verbatim mode only). ``merge`` mode does not
        require ``force`` because it preserves siblings by design.
    dry_run:
        Short-circuit before any filesystem mutation. No backup, no
        parent-directory creation, no atomic swap. The decision the writer
        *would* have made is still returned via :attr:`WriteResult.would_write`.
    """
    spec = _spec_for(client)
    base = Path(root) if root is not None else Path.cwd()
    target = base / spec.target_path
    rendered = render(client)

    if not write and not merge:
        return WriteResult(
            wrote=False, would_write=False, path=target,
            reason="no --write or --merge requested",
        )

    if merge:
        if target.is_file():
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                # Surface as a hard error: --merge cannot proceed safely
                # because we'd either clobber the user's data or guess at
                # repairs. lineno/colno give the user a jump-to-location.
                return WriteResult(
                    wrote=False, would_write=False, path=target,
                    reason=(
                        f"existing file is not valid JSON "
                        f"(line {exc.lineno}, column {exc.colno}): {exc.msg}"
                    ),
                    error=True,
                )
        else:
            existing = {}
        merged = _merge_payload(existing, spec)
        new_content = json.dumps(merged, indent=2) + "\n"
    else:
        new_content = rendered

    if target.is_file():
        current = target.read_text(encoding="utf-8")
        if current == new_content:
            return WriteResult(
                wrote=False, would_write=False, path=target,
                reason="already up to date",
            )
        if not merge and not force:
            return WriteResult(
                wrote=False, would_write=False, path=target,
                reason="refusing to overwrite without --force or --merge",
            )

    if dry_run:
        return WriteResult(
            wrote=False, would_write=True, path=target,
            reason="dry run",
        )

    # Real write below this line. Create parents, back up any existing file,
    # then atomically swap in the new content.
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        backup = target.with_suffix(target.suffix + ".bak")
        os.replace(target, backup)
    _atomic_write(target, new_content)
    return WriteResult(
        wrote=True, would_write=True, path=target,
        reason="merged" if merge else "wrote",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cli_main(argv: list[str]) -> int:
    """Implement ``wd mcp config``. Returns a process exit code.

    Package-internal entry point: :mod:`weld._mcp_cli` owns the ``wd mcp``
    subcommand table and routes here.
    """
    parser = argparse.ArgumentParser(
        prog="wd mcp config",
        description=(
            "Generate a per-client MCP config snippet for the Weld stdio "
            "server. By default prints JSON to stdout; pass --write to "
            "update the client-appropriate config file."
        ),
    )
    parser.add_argument(
        "--client",
        required=True,
        help=(
            "MCP client whose config shape to render. "
            f"Supported: {', '.join(supported_clients())}."
        ),
    )
    parser.add_argument(
        "--write", action="store_true",
        help="Write the snippet to the client-appropriate config file.",
    )
    parser.add_argument(
        "--merge", action="store_true",
        help=(
            "Merge our entry into an existing config file's server map "
            "instead of overwriting (preserves sibling servers)."
        ),
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing config file even if its content differs.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Report what would change without touching the disk.",
    )
    args = parser.parse_args(argv)

    try:
        snippet = render(args.client)
    except UnknownClientError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2

    # Default behaviour: print the snippet. This is the tracked issue acceptance
    # bar and stays true even when --write/--merge/--dry-run are also set,
    # so users can pipe the same output into ``jq`` or a clipboard.
    # sanitize_json_text, not sanitize_terminal_text: the snippet is JSON and
    # the line above promises it stays pipeable into ``jq``, so the escape has
    # to be the \uXXXX form a parser round-trips rather than the \xNN form a
    # human reads.
    sys.stdout.write(sanitize_json_text(snippet))

    if not (args.write or args.merge):
        return 0

    result = write_config(
        args.client,
        write=args.write,
        merge=args.merge,
        force=args.force,
        dry_run=args.dry_run,
    )
    if result.error:
        # Hard failure: e.g. --merge against an unparseable existing file.
        # Scripted callers must see a non-zero exit so they can branch on it
        # rather than scraping stderr.
        sys.stderr.write(
            f"error: cannot update {result.path}: {result.reason}\n"
        )
        return 1
    if result.wrote:
        sys.stderr.write(
            f"wrote {result.path} ({result.reason})\n"
        )
    elif result.would_write:
        sys.stderr.write(
            f"[dry-run] would write {result.path} ({result.reason})\n"
        )
    else:
        sys.stderr.write(
            f"no change to {result.path}: {result.reason}\n"
        )
    return 0
