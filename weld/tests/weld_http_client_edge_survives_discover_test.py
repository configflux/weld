"""Full-discovery regression: http_client file->rpc edges must survive.

Reproduces the ``http_client`` edge-drop bug (same root cause as the
events-family fix, but out of that task's scope): the strategy minted the
``invokes`` edge ``from`` endpoint as ``file:<rel-WITH-.py>`` while
``python_module`` mints the canonical file node as
``file:<rel-WITHOUT-extension>`` (``weld._node_ids.file_id``). Because
``weld._discover_postprocess._clean_and_dedup_edges`` prunes any edge whose
endpoint is not a node id (exact match, no extension normalization), every
``file -> rpc`` binding edge was swept during post-process while the rpc
*nodes* (canonical) survived. The symptom is zero surviving file->rpc invokes
edges after a full discover.

This test drives the *real* discover pipeline (``python_module`` +
``http_client``) over a fixture with static outbound HTTP calls -- both a
full-URL call and a path-only call -- then asserts the ``file -> rpc`` invokes
edges survive the full post-process with canonical extensionless endpoints
that match the ``python_module`` file nodes. It fails (zero surviving binding
edges) before the canonical-endpoint mint fix.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.discover import discover  # noqa: E402

# A fully-wired discover config: the file-node minter (``python_module``)
# plus the outbound-HTTP strategy that binds files to rpc nodes.
_DISCOVER_YAML = (
    "sources:\n"
    "  - strategy: python_module\n"
    "    glob: svc/**/*.py\n"
    "    type: file\n"
    "  - strategy: http_client\n"
    "    glob: svc/**/*.py\n"
    "    type: file\n"
)

# A full-URL client (two rpc nodes) and a path-only client (one rpc node
# plus a dangling-by-design rpc->route link). Every file has a public
# function so ``python_module`` mints its canonical ``file:`` node.
_FILES = {
    "svc/product_client.py": """\
        import httpx

        def fetch_products():
            return httpx.get("https://api.example.com/products")

        def create_product():
            return httpx.post("https://api.example.com/products", json={})
    """,
    "svc/order_client.py": """\
        import requests

        def fetch_orders():
            return requests.get("/orders")
    """,
}


def _build_fixture(root: Path) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
        _DISCOVER_YAML, encoding="utf-8"
    )
    for rel, body in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")


class HttpClientEdgeSurvivesDiscoverTest(unittest.TestCase):
    """file->rpc invokes edges survive the full post-process."""

    graph: dict
    nodes: dict
    edges: list

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        _build_fixture(root)
        cls.graph = discover(root, incremental=False)
        cls.nodes = cls.graph["nodes"]
        cls.edges = cls.graph["edges"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()
        super().tearDownClass()

    def _has_edge(self, etype: str, frm: str, to: str) -> bool:
        return any(
            e["type"] == etype and e["from"] == frm and e["to"] == to
            for e in self.edges
        )

    # -- Baseline: the rpc *nodes* survive today (canonical ids). -----------
    def test_rpc_nodes_present(self) -> None:
        for rid in (
            "rpc:http:out:GET:https://api.example.com/products",
            "rpc:http:out:POST:https://api.example.com/products",
            "rpc:http:out:GET:/orders",
        ):
            self.assertIn(rid, self.nodes, f"missing rpc node {rid}")

    # -- python_module mints canonical extensionless file nodes. ------------
    def test_canonical_file_nodes_present(self) -> None:
        for fid in ("file:svc/product_client", "file:svc/order_client"):
            self.assertIn(fid, self.nodes, f"missing file node {fid}")

    # -- The regression: file->rpc invokes edges must survive. --------------
    def test_full_url_file_to_rpc_edge_survives(self) -> None:
        self.assertTrue(
            self._has_edge(
                "invokes",
                "file:svc/product_client",
                "rpc:http:out:GET:https://api.example.com/products",
            ),
            "http_client file->rpc (GET) invokes edge was swept by post-process",
        )
        self.assertTrue(
            self._has_edge(
                "invokes",
                "file:svc/product_client",
                "rpc:http:out:POST:https://api.example.com/products",
            ),
            "http_client file->rpc (POST) invokes edge was swept by post-process",
        )

    def test_path_url_file_to_rpc_edge_survives(self) -> None:
        self.assertTrue(
            self._has_edge(
                "invokes",
                "file:svc/order_client",
                "rpc:http:out:GET:/orders",
            ),
            "http_client file->rpc (path-only) invokes edge was swept",
        )

    # -- Guard: no surviving file->rpc invokes edge may carry a ``.py``
    #    endpoint. (Also fails if the sweep is later loosened to normalize
    #    extensions rather than fixing the mint side.)
    def test_no_invokes_edge_has_extension_endpoint(self) -> None:
        offenders = [
            e
            for e in self.edges
            if e["type"] == "invokes"
            and str(e["to"]).startswith("rpc:http:")
            and str(e["from"]).endswith(".py")
        ]
        self.assertEqual(
            offenders, [], f"extension-bearing endpoints survived: {offenders}"
        )


if __name__ == "__main__":
    unittest.main()
