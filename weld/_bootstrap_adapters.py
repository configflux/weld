"""Per-host bootstrap adapters (ADR 0054).

`wd bootstrap` already supports ``claude``, ``codex``, and ``copilot``. This
module wires in four additional hosts -- ``cursor``, ``aider``, ``gemini-cli``,
``copilot-cli`` -- using a single canonical body string plus per-host overlay
fragments. The goal is to widen distribution surface without taking on the
multi-file drift problem that competitors hit when they ship one hand-tuned
skill file per host.

Design choices, in line with ADR 0054 §"Decision":

* **One canonical body string** (``_CANONICAL_BODY``) is shared across every
  new host. It is wrapped in an ADR-0033 managed region (``name=retrieval-
  commands``) so re-bootstrap can detect drift and ``--force`` can restore it.
* **Per-host overlays** are short fragments (~10-30 lines) wrapped in
  ``<!-- weld-host:NAME:start --> ... <!-- weld-host:NAME:end -->`` markers.
  These markers are purely visual delimiters in the rendered body; they are
  not parsed by the ADR-0033 managed-region machinery, which keeps the overlay
  surface untyped and easy to evolve without ADR changes.
* **Wiki fallback** (``_WIKI_FALLBACK``) is appended to the skill file when
  ``supports_mcp=False``. It points at ``wd export --format=wiki`` (ADR 0053).
* **MCP configs** delegate to :mod:`weld.mcp_config` for the JSON shape so the
  canonical server-entry payload (``python -m weld.mcp_server``) stays in one
  place. Hosts that do not support MCP (aider today) do not get an MCP file.
* **Aider config** is YAML (``.aider.conf.yml``) plus a ``CONVENTIONS.md``
  skill stanza. ``CONVENTIONS.md`` lives at the repo root because that is
  where aider expects shared conventions per its docs.

The dataclass ``HostBootstrap`` is the contract the registry exposes;
``host_registry()`` returns the four new hosts. ``render_skill`` and the per-
host text functions render their respective files as pure strings so the
writer in :mod:`weld.bootstrap` can feed them through the same managed-region
machinery used for the existing claude/codex/copilot hosts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from weld.mcp_config import render as _render_mcp


# ---------------------------------------------------------------------------
# Shared content
# ---------------------------------------------------------------------------

# Canonical body. Wrapped in a single managed region so re-bootstrap with
# ``--force`` can restore drift inside the retrieval-commands block while
# leaving operator-curated text outside the region intact (ADR 0033).
#
# The body intentionally mirrors the wording of the existing
# ``weld_skill_codex.md`` retrieval-commands block so an agent moving between
# hosts sees identical guidance. The "wd brief <term>" line is the canonical
# fingerprint asserted by the test contract.
_CANONICAL_BODY = """\
# Weld -- Repository Connected Structure

## What it is

`weld` is a connected structure toolkit that maps the entire repository --
code, docs, infra, build, policy, tests, and operations -- into a queryable
graph. Use it to answer "where does this live?", "what depends on what?", and
"which docs or policies apply here?" without grepping across the codebase.

## When to use it

- Before starting work on a new area of the codebase
- When you need to understand dependencies, boundaries, or data flow
- When looking for authoritative documentation or policies
- When checking which build, test, or verification surfaces matter for a change

## Retrieval commands

<!-- weld-managed:start name=retrieval-commands -->
Start with `wd brief` -- it returns a ranked, classified context packet
designed for agent consumption.

| Command | Purpose |
|---------|---------|
| `wd brief <term>` | Default starting point -- ranked context with docs, build surfaces, boundaries |
| `wd query <term>` | Broader tokenized search when brief is too narrow |
| `wd context <node-id>` | Deep dive -- node details plus immediate neighborhood |
| `wd path <from> <to>` | Shortest path between two nodes (dependency/data-flow tracing) |
| `wd find <keyword>` | File-level keyword search using the inverted index |
<!-- weld-managed:end name=retrieval-commands -->

## When to refresh

- After significant code changes (new modules, renamed files, deleted surfaces)
- When `wd stale` reports the graph is behind HEAD
- When `wd prime` suggests a refresh
"""


# Wiki-fallback stanza. Mandatory for hosts where ``supports_mcp=False``
# (aider today). The substring ``wd export --format=wiki`` is asserted by the
# test contract -- changing the exact command spelling is a contract break.
_WIKI_FALLBACK = """\

## MCP fallback: agent-readable wiki

This host does not run an MCP client today. To make the connected structure
agent-readable without MCP, render the graph as a markdown wiki and read it
directly:

```bash
wd export --format=wiki --output=.weld/wiki
```

The wiki is a directory of markdown files with `[[node-id]]` wikilinks. Start
at `.weld/wiki/index.md` and follow the links. Refresh the wiki after running
`wd discover --output .weld/graph.json` when the source moves materially.
"""


# Per-host overlays. Each fragment is wrapped in
# ``<!-- weld-host:NAME:start --> ... <!-- weld-host:NAME:end -->`` markers so
# the test contract can verify the overlay shipped. The fragments stay small
# on purpose: each is a single short stanza of host-specific guidance, not a
# full duplicated skill file.

_HOST_OVERLAYS: dict[str, str] = {
    "cursor": """\

<!-- weld-host:cursor:start -->
## Cursor integration

This file is loaded as a Cursor rule (`.cursor/rules/weld.mdc`). The companion
`.cursor/mcp.json` registers the weld stdio MCP server -- open the Cursor
command palette (Cmd+L) to invoke MCP tools, or run the `wd` CLI directly
from the integrated terminal.
<!-- weld-host:cursor:end -->
""",
    "aider": """\

<!-- weld-host:aider:start -->
## Aider integration

Aider reads project conventions from `CONVENTIONS.md` via `.aider.conf.yml`.
Aider does not run MCP, so use the `wd` CLI from the same shell aider runs
in: `wd brief <term>` for ranked context, `wd context <node-id>` for a
neighborhood walk. The wiki fallback below is the agent-readable surface when
running long autonomous sessions.
<!-- weld-host:aider:end -->
""",
    "gemini-cli": """\

<!-- weld-host:gemini-cli:start -->
## Gemini CLI integration

This file is the weld skill loaded by the Gemini CLI from
`.gemini/skills/weld.md`. The companion `.gemini/mcp.json` registers the
weld stdio MCP server. Use Gemini CLI's `/skills` slash to refresh the skill
list after re-bootstrapping.
<!-- weld-host:gemini-cli:end -->
""",
    "copilot-cli": """\

<!-- weld-host:copilot-cli:start -->
## Copilot CLI integration

This file is the weld skill loaded by the standalone Copilot CLI from
`.copilot/skills/weld.md`. The companion `.copilot/config.json` registers
the weld stdio MCP server. The Copilot CLI binary auths itself; no API key
is needed at the weld layer.
<!-- weld-host:copilot-cli:end -->
""",
}


# ---------------------------------------------------------------------------
# HostBootstrap dataclass + registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HostBootstrap:
    """One entry in the per-host bootstrap registry (ADR 0054).

    Attributes
    ----------
    name:
        Stable host name used as the ``wd bootstrap <NAME>`` subcommand.
    skill_path:
        Path to the host's skill/conventions file relative to the project
        root.
    config_path:
        Path to the host's MCP/config file relative to the project root, or
        ``None`` when the host does not write a separate config file.
    supports_mcp:
        ``True`` when the host runs an MCP client natively. ``False`` for
        hosts that need the wiki fallback (aider today).
    """

    name: str
    skill_path: Path
    config_path: Path | None
    supports_mcp: bool


_REGISTRY: tuple[HostBootstrap, ...] = (
    HostBootstrap(
        name="cursor",
        skill_path=Path(".cursor") / "rules" / "weld.mdc",
        config_path=Path(".cursor") / "mcp.json",
        supports_mcp=True,
    ),
    HostBootstrap(
        name="aider",
        # Aider's repo conventions live at the root CONVENTIONS.md; the
        # companion .aider.conf.yml is the config_path. Aider does not run
        # MCP, so the wiki fallback is mandatory and the skill content lives
        # in CONVENTIONS.md (the file aider auto-loads at startup).
        skill_path=Path("CONVENTIONS.md"),
        config_path=Path(".aider.conf.yml"),
        supports_mcp=False,
    ),
    HostBootstrap(
        name="gemini-cli",
        skill_path=Path(".gemini") / "skills" / "weld.md",
        config_path=Path(".gemini") / "mcp.json",
        supports_mcp=True,
    ),
    HostBootstrap(
        name="copilot-cli",
        skill_path=Path(".copilot") / "skills" / "weld.md",
        config_path=Path(".copilot") / "config.json",
        supports_mcp=True,
    ),
)


def host_registry() -> tuple[HostBootstrap, ...]:
    """Return the per-host registry in stable order.

    The order matches the ADR 0054 integration matrix. New hosts append to the
    end so existing scripts that iterate the registry see deterministic ordering.
    """
    return _REGISTRY


def host_spec(name: str) -> HostBootstrap:
    """Return the registry entry for *name*, or raise ``KeyError``."""
    for spec in _REGISTRY:
        if spec.name == name:
            return spec
    raise KeyError(name)


# ---------------------------------------------------------------------------
# Skill / config renderers
# ---------------------------------------------------------------------------

def render_skill(name: str) -> str:
    """Render the full skill (or conventions) file for *name*.

    The output is ``canonical body + host overlay + optional wiki fallback``.
    Hosts without native MCP support get the wiki fallback appended; hosts
    with MCP do not, because they already get an MCP config file written
    separately.
    """
    spec = host_spec(name)
    overlay = _HOST_OVERLAYS.get(name)
    if overlay is None:  # pragma: no cover - registry/overlay drift
        raise KeyError(f"no overlay registered for host {name!r}")
    body = _CANONICAL_BODY + overlay
    if not spec.supports_mcp:
        body += _WIKI_FALLBACK
    return body


# Shared rendering helper -- the three MCP-supporting hosts all use the
# canonical ``mcpServers``/``weld`` JSON shape from :mod:`weld.mcp_config`.
# Centralising it here avoids per-host drift in the JSON layout.
def _mcp_json_for_client(client: str) -> str:
    """Return the formatted JSON snippet for *client* (cursor/claude shape).

    Reuses :func:`weld.mcp_config.render` so the server entry stays in lockstep
    with the rest of the MCP config surface (ADR 0023).
    """
    return _render_mcp(client)


def cursor_mcp_text() -> str:
    """Render ``.cursor/mcp.json`` content."""
    return _mcp_json_for_client("cursor")


def gemini_mcp_text() -> str:
    """Render ``.gemini/mcp.json`` content.

    Gemini CLI follows the standard ``mcpServers`` JSON shape (same key name
    as Cursor / Claude), so we reuse the cursor renderer rather than adding a
    near-duplicate client registry entry.
    """
    return _mcp_json_for_client("cursor")


def copilot_cli_config_text() -> str:
    """Render ``.copilot/config.json`` content.

    The standalone Copilot CLI accepts the standard ``mcpServers`` shape; the
    config file path differs (``.copilot/config.json`` rather than the
    ``mcp.json`` siblings used by cursor/gemini).
    """
    return _mcp_json_for_client("cursor")


# Aider's config is YAML and points at CONVENTIONS.md (the file aider
# auto-loads at startup). The YAML stays in code rather than as a template
# file because it is two lines and pulling in PyYAML for output would be
# heavier than the saved verbatim copy.
#
# The substring "mcp" (case-insensitive) deliberately does not appear
# anywhere in the file content -- the test contract asserts the YAML has no
# such stanza, which is the correct shape for a host that has no native
# tool-call protocol. The CONVENTIONS.md skill file carries the wiki
# fallback that substitutes for the missing protocol.
_AIDER_CONFIG_YAML = """\
# Aider config -- generated by wd bootstrap (ADR 0054).
#
# CONVENTIONS.md carries the weld onboarding text. Aider auto-loads any
# file listed under `read:` at the start of every session. The wiki
# fallback in CONVENTIONS.md tells the agent how to reach the connected
# structure when no tool-call protocol is available.
read:
  - CONVENTIONS.md
"""


def aider_config_text() -> str:
    """Render ``.aider.conf.yml`` content."""
    return _AIDER_CONFIG_YAML


# ---------------------------------------------------------------------------
# Convenience: config file payload by host
# ---------------------------------------------------------------------------

def render_config(name: str) -> str | None:
    """Return the config-file payload for host *name*, or ``None``.

    The writer in :mod:`weld.bootstrap` calls this for hosts whose
    ``config_path`` is set. Hosts without a config file (none today; reserved
    for future hosts) get ``None`` back so the writer can skip.
    """
    spec = host_spec(name)
    if spec.config_path is None:
        return None
    if name == "cursor":
        return cursor_mcp_text()
    if name == "gemini-cli":
        return gemini_mcp_text()
    if name == "copilot-cli":
        return copilot_cli_config_text()
    if name == "aider":
        return aider_config_text()
    raise KeyError(f"no config renderer registered for host {name!r}")  # pragma: no cover


# JSON shape sanity check kept inline so callers can validate the output of
# the MCP adapters without re-parsing them. Used by the tests only.
def _is_valid_mcp_json(text: str) -> bool:  # pragma: no cover - test helper
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    return (
        isinstance(payload, dict)
        and "mcpServers" in payload
        and "weld" in payload["mcpServers"]
    )
