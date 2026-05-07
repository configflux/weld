"""Surface derivation and bucketing for blast-radius analysis.

Pure helpers split out of :mod:`weld.impact_core` so the impact module
stays well under the 400-line cap. Owns the rules that decide which
``affected_surfaces`` bucket a reached node belongs to, including the
language-agnostic ``tests`` bucket.
"""

from __future__ import annotations

import posixpath
from pathlib import Path

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
    cleaned = path.replace("\\", "/").strip()
    if not cleaned:
        return ""
    normalized = posixpath.normpath(cleaned)
    return "" if normalized == "." else normalized


def _surface_bucket(node: dict) -> str | None:
    node_type = node.get("type")
    if node_type == "command":
        return "cli_commands"
    if node_type == "tool":
        return "mcp_tools"
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
    props = node.get("props") or {}
    file_path = _normalize_path(str(props.get("file", "")))
    qualname = str(props.get("qualname", ""))
    if file_path not in {"weld/mcp_helpers.py", "weld/mcp_server.py"}:
        return None
    if not qualname.startswith("weld_"):
        return None
    return {
        "id": f"tool:{qualname}",
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
    if surfaces["api_endpoints"] or surfaces["entrypoints"] or surfaces["boundaries"]:
        return "HIGH"
    if surfaces["cli_commands"] or surfaces["mcp_tools"]:
        return "MEDIUM"
    return "LOW"
