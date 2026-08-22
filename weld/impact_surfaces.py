"""Surface derivation and bucketing for blast-radius analysis.

Pure helpers split out of :mod:`weld.impact_core` so the impact module
stays well under the 400-line cap. Owns the rules that decide which
``affected_surfaces`` bucket a reached node belongs to, including the
language-agnostic ``tests`` bucket.
"""

from __future__ import annotations

import posixpath
from pathlib import Path

from weld._rel_path import canonical_rel_path

#: Id namespace for MCP tools *synthesised* by :func:`_derived_mcp_tool`, kept
#: distinct from the ``tool:`` namespace discovery mints for repo scripts so the
#: two can never collide on a bare stem (bd hfmn).
MCP_TOOL_ID_PREFIX = "mcp-tool:"

_API_ENDPOINT_TYPES = frozenset(["route", "rpc", "channel"])
_ENTRYPOINT_FILES = frozenset(["weld/cli.py", "weld/__main__.py"])
_COMMAND_MODULE_EXCLUSIONS = frozenset(["cli", "graph", "__main__"])

# A reached node lands in ``affected_surfaces.tests`` if its node type matches
# one of these, or if it is a ``file:*`` whose ``props.role`` is ``"test"``
# or whose path matches the filename convention (``*_test.py``). The match is
# language-agnostic by design; Layer C3 follow-up work extends the convention
# list to ``*.test.ts`` / ``*Test.java`` / etc. without changing the engine.
_TEST_NODE_TYPES = frozenset(["test-target", "test-suite"])


def _normalize_path(path: str) -> str:
    """Return *path* as the canonical repo-relative form used by the buckets.

    Three folds, and only one of them is about separators:

    * the separator fold, delegated to :func:`weld._rel_path.canonical_rel_path`
      -- platform-aware, so the exact identity on POSIX. It used to be an
      unconditional ``replace("\\\\", "/")`` here, which was the right call while
      the graph could hand this function either spelling; since bd 244j the
      stored artifact is canonicalized at write time, so folding a backslash on
      POSIX no longer repairs anything and only misreads a file legitimately
      named ``a\\b.py``. Off POSIX the fold still applies, which is where an
      artifact written by a pre-244j weld could still carry a native spelling
      (bd 3x85);
    * ``strip()`` and the ``posixpath.normpath`` pass, which are not about
      spelling at all -- they absorb whitespace padding and ``./a`` from a
      strategy that built its anchor by concatenation, and they stay;
    * ``"."`` maps to ``""``, so "the repo root" is falsy like a missing anchor
      rather than a path that ``startswith("weld/")`` comparisons would miss on.
    """
    cleaned = canonical_rel_path(path).strip()
    if not cleaned:
        return ""
    normalized = posixpath.normpath(cleaned)
    return "" if normalized == "." else normalized


def _surface_bucket(node: dict) -> str | None:
    node_type = node.get("type")
    if node_type == "command":
        return "cli_commands"
    if node_type == "tool":
        # Node type alone stopped identifying an MCP tool at ADR 0106, which
        # path-qualified ``tool:`` ids and widened discovery to ``tools/**``:
        # the type went from 3 nodes (all MCP tools) to 49, of which 46 are
        # repo shell/python scripts. Bucketing all of them as ``mcp_tools``
        # scored every change to any ``tools/`` script MEDIUM through a bucket
        # it did not belong in (bd 7rla).
        #
        # Provenance separates them, because the two arrive by different
        # routes: an MCP tool is *synthesised* here by ``_derived_mcp_tool``
        # from a ``weld/mcp_helpers.py`` / ``weld/mcp_server.py`` symbol and
        # carries ``derived_from``; a script is *discovered* and never does.
        # Provenance stays the discriminator even though bd hfmn has since
        # moved derived tools to their own ``mcp-tool:`` namespace, so the two
        # ids can no longer collide. An id-prefix test would still be the
        # fragile version: it would couple bucketing to a spelling, and the
        # spelling is exactly what turned out to be untrustworthy here.
        props = node.get("props") or {}
        return "mcp_tools" if props.get("derived_from") else "repo_tools"
    if node_type in _API_ENDPOINT_TYPES:
        return "api_endpoints"
    if node_type == "entrypoint":
        return "entrypoints"
    if node_type == "boundary":
        return "boundaries"
    return None


def _is_test_node(node: dict) -> bool:
    """Decide whether *node* belongs in ``affected_surfaces.tests``."""
    node_type = node.get("type")
    if node_type in _TEST_NODE_TYPES:
        return True
    props = node.get("props") or {}
    if node_type == "file" and props.get("role") == "test":
        return True
    file_path = _normalize_path(str(props.get("file", "")))
    if file_path.endswith("_test.py"):
        return True
    return False


def _test_surface_entry(node: dict) -> dict:
    props = node.get("props") or {}
    return {
        "id": node["id"],
        "type": node.get("type", ""),
        "file": _normalize_path(str(props.get("file", ""))),
        "hop": node.get("hop", 0),
    }


def _derived_cli_command(node: dict) -> dict | None:
    props = node.get("props") or {}
    file_path = _normalize_path(str(props.get("file", "")))
    qualname = props.get("qualname")
    if not file_path.startswith("weld/") or not file_path.endswith(".py"):
        return None
    if qualname != "main":
        return None
    module_name = Path(file_path).stem
    if module_name in _COMMAND_MODULE_EXCLUSIONS:
        return None
    return {
        "id": f"command:wd {module_name}",
        "type": "command",
        "label": f"wd {module_name}",
        "props": {"derived_from": node["id"], "file": file_path},
        "hop": node.get("hop", 0),
    }


def _derived_entrypoint(node: dict) -> dict | None:
    props = node.get("props") or {}
    file_path = _normalize_path(str(props.get("file", "")))
    qualname = props.get("qualname")
    if file_path in _ENTRYPOINT_FILES and qualname == "main":
        return {
            "id": "entrypoint:wd",
            "type": "entrypoint",
            "label": "wd entrypoint",
            "props": {"derived_from": node["id"], "file": file_path},
            "hop": node.get("hop", 0),
        }
    return None


def _derived_mcp_tool(node: dict) -> dict | None:
    """Synthesise the MCP tool a ``weld_*`` handler symbol publishes.

    The id is namespaced ``mcp-tool:`` rather than ``tool:`` (bd hfmn). Two
    independent mechanisms mint tool ids: discovery, via ADR 0106's
    :func:`weld._node_ids.tool_id`, which leaves a ROOT-level script a bare stem
    (``tool:install``); and this function, which synthesises from a qualname.
    Sharing one namespace, a root-level ``weld_trace.sh`` would mint
    ``tool:weld_trace`` character-for-character -- and while
    :func:`_surface_bucket` discriminates the two on ``derived_from`` provenance
    and so buckets both correctly even under collision, anything addressing a
    node BY ID (``wd context tool:weld_trace``) could not say which was meant.

    Namespacing the *derived* side is the small half of that fix: these ids
    exist only inside the impact envelope and are never stored in the graph, so
    nothing migrates. Path-qualifying the discovered side instead
    (``tool:install`` -> ``tool:./install``) would rewrite ids that *are* in the
    graph, and would reverse the bare-stem spelling ADR 0106 just landed.
    """
    props = node.get("props") or {}
    file_path = _normalize_path(str(props.get("file", "")))
    qualname = str(props.get("qualname", ""))
    if file_path not in {"weld/mcp_helpers.py", "weld/mcp_server.py"}:
        return None
    if not qualname.startswith("weld_"):
        return None
    return {
        "id": f"{MCP_TOOL_ID_PREFIX}{qualname}",
        "type": "tool",
        "label": qualname,
        "props": {"derived_from": node["id"], "file": file_path},
        "hop": node.get("hop", 0),
    }


def _derived_surfaces(node: dict) -> list[dict]:
    surfaces: list[dict] = []
    for derived in (
        _derived_cli_command(node),
        _derived_entrypoint(node),
        _derived_mcp_tool(node),
    ):
        if derived is not None:
            surfaces.append(derived)
    return surfaces


def _empty_surfaces() -> dict[str, list[dict]]:
    return {
        "cli_commands": [],
        "mcp_tools": [],
        "repo_tools": [],
        "api_endpoints": [],
        "entrypoints": [],
        "boundaries": [],
        "tests": [],
    }


def _collect_surfaces(nodes: list[dict]) -> dict[str, list[dict]]:
    """Bucket reached *nodes* into the public-surfaces envelope.

    Each node fans out into derived surfaces (e.g. a Python ``main`` symbol
    fans out into the corresponding ``command:`` node) before bucketing. The
    ``tests`` bucket is built off the original node only -- derived
    surfaces never represent a test.
    """
    surfaces = _empty_surfaces()
    seen: dict[str, set[str]] = {key: set() for key in surfaces}
    for node in nodes:
        expanded = [node, *_derived_surfaces(node)]
        for candidate in expanded:
            bucket = _surface_bucket(candidate)
            if bucket is None:
                continue
            if candidate["id"] in seen[bucket]:
                continue
            seen[bucket].add(candidate["id"])
            surfaces[bucket].append(candidate)
        if _is_test_node(node) and node["id"] not in seen["tests"]:
            seen["tests"].add(node["id"])
            surfaces["tests"].append(_test_surface_entry(node))
    return surfaces


def _risk_level(surfaces: dict[str, list[dict]]) -> str:
    """Score the blast radius off the *published* surfaces it reaches.

    ``repo_tools`` is deliberately not a trigger. Every other bucket names a
    contract somebody outside the repo can depend on -- a CLI subcommand, an
    MCP tool, an HTTP route, an entrypoint, a module boundary. A repo script is
    internal development infrastructure, and counting it was the unearned
    MEDIUM on 40-odd ``tools/`` scripts that bd 7rla reported. What those
    scripts are is still in the envelope, under their own bucket, and their
    dependents are still enumerated; they just do not move the verdict.
    """
    if surfaces["api_endpoints"] or surfaces["entrypoints"] or surfaces["boundaries"]:
        return "HIGH"
    if surfaces["cli_commands"] or surfaces["mcp_tools"]:
        return "MEDIUM"
    return "LOW"
