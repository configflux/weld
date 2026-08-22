"""Tests for federated MCP tool extensions (brief/stale/callers/references).

Pins the behavior of the four MCP tool handlers that were extended to
work across federated children via :mod:`weld.federation_tools`:

* ``weld_brief`` -- includes child matches via ``FederatedGraph.query``.
* ``weld_stale`` -- reports per-child staleness or graceful degradation. Since
  ADR 0100 it delegates to the product shaper the CLI uses rather than to a
  helper here, so what this module pins is the *contract* -- the projection and
  its lifecycle passthrough. Byte-identity against ``wd stale --json`` is
  pinned separately, in :mod:`weld.tests.weld_read_parity_federated_test`.
* ``weld_callers`` -- resolves prefixed symbol IDs within children.
* ``weld_references`` -- fans out bare-name search across all children.

Each handler must also work unchanged in a single-repo (non-federated)
workspace.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld import mcp_server
from weld.contract import SCHEMA_VERSION
from weld.workspace import (
    UNIT_SEPARATOR, ChildEntry, WorkspaceConfig, dump_workspaces_yaml,
)

_TS = "2026-04-15T21:00:00+00:00"


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo_root),
        capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _init_repo(repo_root: Path) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _graph_payload(
    nodes: dict, edges: list[dict] | None = None, *, sv: int = 1,
) -> dict:
    return {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS,
                 "schema_version": sv},
        "nodes": nodes, "edges": edges or [],
    }


def _write_graph(repo_root: Path, payload: dict) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def _write_workspaces(root: Path, children: list[ChildEntry]) -> None:
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")


def _write_root_graph(
    root: Path, children: list[str], edges: list[dict] | None = None,
) -> None:
    nodes = {f"repo:{n}": {"type": "repo", "label": n,
                           "props": {"path": n}} for n in children}
    _write_graph(root, _graph_payload(nodes, edges, sv=2))


# -- Fixtures ---------------------------------------------------------------

def _build_store_workspace(root: Path) -> None:
    """Two-child workspace with Store entities (brief tests)."""
    for name in ("svc-api", "svc-auth"):
        child = _init_repo(root / name)
        _write_graph(child, _graph_payload({
            "entity:Store": {
                "type": "entity", "label": "Store",
                "props": {"file": f"src/{name}/store.py",
                           "description": f"{name} Store."}}}))
    _write_root_graph(root, ["svc-api", "svc-auth"])
    _write_workspaces(root, [
        ChildEntry(name="svc-api", path="svc-api"),
        ChildEntry(name="svc-auth", path="svc-auth")])


def _build_symbol_workspace(root: Path) -> None:
    """Two-child workspace with symbols and calls edges."""
    core = _init_repo(root / "lib-core")
    auth = _init_repo(root / "lib-auth")
    _write_graph(core, _graph_payload({
        "symbol:py:lib_core:load_config": {
            "type": "symbol", "label": "load_config",
            "props": {"file": "src/config.py",
                       "qualname": "lib_core.load_config"}},
        "symbol:py:lib_core:validate": {
            "type": "symbol", "label": "validate",
            "props": {"file": "src/validate.py",
                       "qualname": "lib_core.validate"}},
    }, edges=[{"from": "symbol:py:lib_core:load_config",
               "to": "symbol:py:lib_core:validate", "type": "calls"}]))
    _write_graph(auth, _graph_payload({
        "symbol:py:lib_auth:authenticate": {
            "type": "symbol", "label": "authenticate",
            "props": {"file": "src/auth.py",
                       "qualname": "lib_auth.authenticate"}},
        "symbol:unresolved:validate": {
            "type": "symbol", "label": "validate",
            "props": {"qualname": "validate"}},
    }, edges=[{"from": "symbol:py:lib_auth:authenticate",
               "to": "symbol:unresolved:validate", "type": "calls"}]))
    _write_root_graph(root, ["lib-core", "lib-auth"])
    _write_workspaces(root, [
        ChildEntry(name="lib-core", path="lib-core"),
        ChildEntry(name="lib-auth", path="lib-auth")])


def _build_sentinel_workspace(root: Path) -> None:
    """Workspace with all four child sentinel states."""
    present = _init_repo(root / "repo-present")
    _write_graph(present, _graph_payload({"entity:Store": {
        "type": "entity", "label": "Store",
        "props": {"file": "s.py", "description": "Present Store."}}}))
    _init_repo(root / "repo-uninitialized")  # no graph
    corrupt = _init_repo(root / "repo-corrupt")
    (corrupt / ".weld").mkdir(parents=True, exist_ok=True)
    (corrupt / ".weld" / "graph.json").write_text("{bad\n", encoding="utf-8")
    names = ["repo-present", "repo-uninitialized",
             "repo-missing", "repo-corrupt"]
    _write_root_graph(root, names)
    _write_workspaces(root, [ChildEntry(name=n, path=n) for n in names])


# -- Brief -------------------------------------------------------------------

class McpBriefFederationTest(unittest.TestCase):

    def test_brief_returns_matches_from_children(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_store_workspace(root)
            result = mcp_server.dispatch(
                "weld_brief", {"area": "Store", "limit": 20}, root=root)
            self.assertEqual(result["brief_version"], 2)
            all_ids = set()
            for b in ("primary", "interfaces", "docs", "build",
                      "boundaries"):
                all_ids.update(n["id"] for n in result[b])
            self.assertIn(f"svc-api{UNIT_SEPARATOR}entity:Store", all_ids)
            self.assertIn(f"svc-auth{UNIT_SEPARATOR}entity:Store", all_ids)

    def test_brief_single_repo_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root, _graph_payload({"entity:Store": {
                "type": "entity", "label": "Store",
                "props": {"file": "s.py"}}}))
            result = mcp_server.dispatch(
                "weld_brief", {"area": "Store", "limit": 20}, root=root)
            all_ids = set()
            for b in ("primary", "interfaces", "docs", "build",
                      "boundaries"):
                all_ids.update(n["id"] for n in result[b])
            self.assertIn("entity:Store", all_ids)
            self.assertTrue(
                all(UNIT_SEPARATOR not in i for i in all_ids))


# -- Stale -------------------------------------------------------------------

class McpStaleFederationTest(unittest.TestCase):

    def test_stale_federated_reports_children(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _build_symbol_workspace(root)
            (root / "m.txt").write_text("x\n", encoding="utf-8")
            _git(root, "add", "m.txt")
            _git(root, "commit", "-q", "-m", "m")
            result = mcp_server.dispatch("weld_stale", {}, root=root)
            # ADR 0100: ``children`` is the CLI projection -- a name-sorted
            # list of {name, state, reason, commits_behind} -- not the map of
            # raw per-child Graph.stale() results this tool used to return.
            children = result["children"]
            self.assertEqual([c["name"] for c in children],
                             ["lib-auth", "lib-core"])
            for child in children:
                self.assertEqual(
                    set(child), {"name", "state", "reason", "commits_behind"})
                # These fixture graphs record no discovered-from SHA, so the
                # oracle cannot date them and conservatively reports stale.
                self.assertEqual(child["state"], "stale")
                self.assertEqual(child["reason"], "unknown_sha")
            # ADR 0066 §2: a stale child raises the top-level agent gate.
            self.assertTrue(result["stale"])
            # Branch identity (ADR 0096 §3) is reported on every weld_stale
            # return path, federated included -- a root whose answer spans
            # child repos is precisely one worth naming the checkout of.
            self.assertEqual(result["branch"], _git(root, "rev-parse",
                                                    "--abbrev-ref", "HEAD"))
            self.assertIn("graph_branch", result)

    def test_stale_single_repo_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _write_graph(root, _graph_payload({"entity:Foo": {
                "type": "entity", "label": "Foo",
                "props": {"file": "f.py"}}}))
            (root / "m.txt").write_text("x\n", encoding="utf-8")
            _git(root, "add", "m.txt")
            _git(root, "commit", "-q", "-m", "m")
            result = mcp_server.dispatch("weld_stale", {}, root=root)
            self.assertIn("stale", result)
            self.assertNotIn("children", result)

    def test_stale_federated_missing_child_degrades(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _build_sentinel_workspace(root)
            result = mcp_server.dispatch("weld_stale", {}, root=root)
            by_name = {c["name"]: c for c in result["children"]}
            # ADR 0100: the lifecycle sentinels survive the projection -- the
            # token that used to arrive as {"status": ...} now arrives in
            # ``state``/``reason``. ADR 0066 §1 rule 1 still holds: an absent
            # or unreadable child is not "behind", so none of them is stale.
            for name in ("missing", "uninitialized", "corrupt"):
                entry = by_name[f"repo-{name}"]
                self.assertEqual(entry["state"], name)
                self.assertEqual(entry["reason"], name)
            self.assertEqual(by_name["repo-present"]["state"], "stale")


# -- Callers -----------------------------------------------------------------

class McpCallersFederationTest(unittest.TestCase):

    def test_callers_federated_finds_child_callers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_symbol_workspace(root)
            target = f"lib-core{UNIT_SEPARATOR}symbol:py:lib_core:validate"
            result = mcp_server.dispatch(
                "weld_callers", {"symbol_id": target, "depth": 1},
                root=root)
            caller_ids = {c["id"] for c in result["callers"]}
            expected = (f"lib-core{UNIT_SEPARATOR}"
                        "symbol:py:lib_core:load_config")
            self.assertIn(expected, caller_ids)

    def test_callers_federated_seeds_and_targets_are_prefixed(self) -> None:
        """bd jz65r: ``seeds`` (top-level) and per-caller ``targets``
        (depth 1) must carry the same child prefix as ``id`` -- the exact
        gap bd nyoks found in ``_prefix_node`` for ``references()``'s
        ``targets``, checked here for ``callers()``'s own new fields from
        the start. ``federated_callers``'s prefixed-child branch rebuilds
        the envelope key-by-key rather than reusing ``raw`` wholesale, so a
        field it does not explicitly copy silently disappears."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_symbol_workspace(root)
            prefixed_seed = f"lib-auth{UNIT_SEPARATOR}symbol:unresolved:validate"
            result = mcp_server.dispatch(
                "weld_callers",
                {"symbol_id": prefixed_seed, "depth": 1}, root=root)
            self.assertEqual([prefixed_seed], result["seeds"])
            auth_caller = (
                f"lib-auth{UNIT_SEPARATOR}symbol:py:lib_auth:authenticate")
            by_id = {c["id"]: c for c in result["callers"]}
            self.assertEqual([prefixed_seed], by_id[auth_caller]["targets"])

    def test_callers_single_repo_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root, _graph_payload({
                "symbol:py:m:foo": {"type": "symbol", "label": "foo",
                                    "props": {"qualname": "m.foo"}},
                "symbol:py:m:bar": {"type": "symbol", "label": "bar",
                                    "props": {"qualname": "m.bar"}},
            }, edges=[{"from": "symbol:py:m:bar",
                       "to": "symbol:py:m:foo", "type": "calls"}]))
            result = mcp_server.dispatch(
                "weld_callers",
                {"symbol_id": "symbol:py:m:foo", "depth": 1}, root=root)
            caller_ids = {c["id"] for c in result["callers"]}
            self.assertIn("symbol:py:m:bar", caller_ids)
            self.assertTrue(
                all(UNIT_SEPARATOR not in c for c in caller_ids))

    def test_callers_federated_not_found(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_symbol_workspace(root)
            result = mcp_server.dispatch(
                "weld_callers",
                {"symbol_id": "nonexistent:symbol"}, root=root)
            self.assertIn("error", result)


# -- References --------------------------------------------------------------

class McpReferencesFederationTest(unittest.TestCase):

    def test_references_federated_finds_across_children(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_symbol_workspace(root)
            result = mcp_server.dispatch(
                "weld_references", {"symbol_name": "validate"},
                root=root)
            match_ids = {m["id"] for m in result["matches"]}
            self.assertIn(
                f"lib-core{UNIT_SEPARATOR}symbol:py:lib_core:validate",
                match_ids)
            self.assertIn(
                f"lib-auth{UNIT_SEPARATOR}symbol:unresolved:validate",
                match_ids)
            caller_ids = {c["id"] for c in result["callers"]}
            self.assertIn(
                f"lib-auth{UNIT_SEPARATOR}"
                "symbol:py:lib_auth:authenticate", caller_ids)

    def test_references_federated_callers_target_their_own_prefixed_match(
        self,
    ) -> None:
        """Each federated caller's ``targets`` (bd nyoks) names its OWN
        child's match, prefixed the same way the caller's own ``id`` is --
        a caller row mixing a prefixed ``id`` with an unprefixed target id
        would be self-inconsistent with the top-level ``matches`` list."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_symbol_workspace(root)
            result = mcp_server.dispatch(
                "weld_references", {"symbol_name": "validate"},
                root=root)
            by_id = {c["id"]: c for c in result["callers"]}
            auth_caller = (
                f"lib-auth{UNIT_SEPARATOR}symbol:py:lib_auth:authenticate")
            core_caller = (
                f"lib-core{UNIT_SEPARATOR}symbol:py:lib_core:load_config")
            self.assertEqual(
                [f"lib-auth{UNIT_SEPARATOR}symbol:unresolved:validate"],
                by_id[auth_caller]["targets"])
            self.assertEqual(
                [f"lib-core{UNIT_SEPARATOR}symbol:py:lib_core:validate"],
                by_id[core_caller]["targets"])

    def test_references_single_repo_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root, _graph_payload({
                "symbol:py:m:validate": {
                    "type": "symbol", "label": "validate",
                    "props": {"qualname": "m.validate"}},
                "symbol:py:m:caller": {
                    "type": "symbol", "label": "caller",
                    "props": {"qualname": "m.caller"}},
            }, edges=[{"from": "symbol:py:m:caller",
                       "to": "symbol:py:m:validate",
                       "type": "calls"}]))
            result = mcp_server.dispatch(
                "weld_references", {"symbol_name": "validate"},
                root=root)
            match_ids = {m["id"] for m in result["matches"]}
            self.assertIn("symbol:py:m:validate", match_ids)
            self.assertTrue(
                all(UNIT_SEPARATOR not in i for i in match_ids))


if __name__ == "__main__":
    unittest.main()
