"""Eager federation inverted-index aggregation (ADR 0063).

Asserts the opt-in eager path produces the same federation query
``matches`` set as the lazy default, and that the env-var fallback
toggles the path on. The bench-style p50/p95 comparison lives in the
bench suite (``weld/tests/bench/weld_federation_eager_test.py``).
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._sqlite_reader import SqliteBackedGraph  # noqa: E402
from weld.federation import FederatedGraph  # noqa: E402
from weld.tests._federation_sqlite_fixtures import (  # noqa: E402
    graph_payload,
    make_workspace,
)


def _child_payload(label: str) -> dict:
    return graph_payload({
        f"service:{label}": {
            "type": "service",
            "label": label,
            "props": {
                "file": f"{label}/main.py",
                "description": f"{label} service surface",
            },
        },
        f"symbol:{label}_helper": {
            "type": "symbol",
            "label": f"{label}_helper",
            "props": {"description": f"helper for {label}"},
        },
        f"route:{label}_api": {
            "type": "route",
            "label": f"{label}_api",
            "props": {"file": f"{label}/api.py"},
        },
    })


class FederationEagerQueryTest(unittest.TestCase):
    """ADR 0063: opt-in eager aggregation; default stays lazy."""

    def test_eager_flag_off_by_default(self) -> None:
        """Constructor with no flag uses the existing lazy per-query path."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[("alpha", _child_payload("alpha"), True)],
            )
            fg = FederatedGraph(root)
            try:
                self.assertFalse(fg.eager_index_active)
            finally:
                fg.close()

    def test_eager_flag_constructor(self) -> None:
        """``eager_index=True`` flips the eager path on at construction."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[("alpha", _child_payload("alpha"), True)],
            )
            fg = FederatedGraph(root, eager_index=True)
            try:
                self.assertTrue(fg.eager_index_active)
                # The aggregated index must include the sqlite child.
                self.assertIn("alpha", fg._eager_index.eager_children)
            finally:
                fg.close()

    def test_eager_flag_env_var(self) -> None:
        """``WELD_FEDERATION_EAGER=1`` flips eager on with no constructor arg."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[("alpha", _child_payload("alpha"), True)],
            )
            old = os.environ.get("WELD_FEDERATION_EAGER")
            os.environ["WELD_FEDERATION_EAGER"] = "1"
            try:
                fg = FederatedGraph(root)
                try:
                    self.assertTrue(fg.eager_index_active)
                finally:
                    fg.close()
            finally:
                if old is None:
                    os.environ.pop("WELD_FEDERATION_EAGER", None)
                else:
                    os.environ["WELD_FEDERATION_EAGER"] = old

    def test_eager_env_var_truthy_values(self) -> None:
        """Documented truthy values turn the flag on; others do not."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[("alpha", _child_payload("alpha"), True)],
            )
            for value, expected in (
                ("1", True), ("true", True), ("True", True),
                ("yes", True), ("YES", True), ("on", True), ("ON", True),
                ("0", False), ("false", False), ("", False),
                ("no", False), ("off", False), ("anything-else", False),
            ):
                old = os.environ.get("WELD_FEDERATION_EAGER")
                os.environ["WELD_FEDERATION_EAGER"] = value
                try:
                    fg = FederatedGraph(root)
                    try:
                        self.assertEqual(
                            fg.eager_index_active, expected,
                            f"WELD_FEDERATION_EAGER={value!r} should yield {expected}",
                        )
                    finally:
                        fg.close()
                finally:
                    if old is None:
                        os.environ.pop("WELD_FEDERATION_EAGER", None)
                    else:
                        os.environ["WELD_FEDERATION_EAGER"] = old

    def test_eager_constructor_arg_overrides_env(self) -> None:
        """Explicit ``eager_index=False`` wins over a truthy env var."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[("alpha", _child_payload("alpha"), True)],
            )
            old = os.environ.get("WELD_FEDERATION_EAGER")
            os.environ["WELD_FEDERATION_EAGER"] = "1"
            try:
                fg = FederatedGraph(root, eager_index=False)
                try:
                    self.assertFalse(fg.eager_index_active)
                finally:
                    fg.close()
            finally:
                if old is None:
                    os.environ.pop("WELD_FEDERATION_EAGER", None)
                else:
                    os.environ["WELD_FEDERATION_EAGER"] = old

    def test_eager_matches_lazy_set(self) -> None:
        """Eager and lazy paths return the same federation match set."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[
                    ("alpha", _child_payload("alpha"), True),
                    ("beta", _child_payload("beta"), True),
                    ("gamma", _child_payload("gamma"), True),
                ],
            )
            for term in (
                "service",
                "route",
                "alpha_helper",  # exact-symbol style.
                "service alpha",  # multi-token strict-AND.
                "no_match_term_xyz",
            ):
                with self.subTest(term=term):
                    fg_lazy = FederatedGraph(root, eager_index=False)
                    try:
                        lazy_ids = {
                            m["id"]
                            for m in fg_lazy.query(term, limit=50)["matches"]
                        }
                    finally:
                        fg_lazy.close()

                    fg_eager = FederatedGraph(root, eager_index=True)
                    try:
                        eager_ids = {
                            m["id"]
                            for m in fg_eager.query(term, limit=50)["matches"]
                        }
                    finally:
                        fg_eager.close()

                    self.assertEqual(
                        lazy_ids, eager_ids,
                        f"term={term!r} match sets differ:"
                        f" only_lazy={lazy_ids - eager_ids}"
                        f" only_eager={eager_ids - lazy_ids}",
                    )

    def test_eager_skips_stale_sidecar_children(self) -> None:
        """A stale-sidecar child is not in ``eager_children``; it falls back."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[
                    ("alpha", _child_payload("alpha"), True),
                    ("beta", _child_payload("beta"), True),
                ],
            )
            # Make beta's sidecar stale by appending to its graph.json.
            beta_graph = root / "beta" / ".weld" / "graph.json"
            beta_graph.write_bytes(beta_graph.read_bytes() + b"\n")

            fg = FederatedGraph(root, eager_index=True)
            try:
                self.assertIn("alpha", fg._eager_index.eager_children)
                self.assertNotIn("beta", fg._eager_index.eager_children)
                # A query for beta still works via the lazy/JSON fallback.
                ids = {
                    m["id"] for m in fg.query("beta", limit=10)["matches"]
                }
                self.assertIn("beta\x1fservice:beta", ids)
            finally:
                fg.close()

    def test_eager_skips_when_no_sqlite_children(self) -> None:
        """No sqlite-fresh children: eager is a no-op (no aggregation work)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # write_sidecars=False -> JSON only, no graph.db files.
            make_workspace(
                root,
                children=[("alpha", _child_payload("alpha"), False)],
            )
            fg = FederatedGraph(root, eager_index=True)
            try:
                # Flag is "active" but no children covered.
                self.assertTrue(fg.eager_index_active)
                self.assertEqual(set(), fg._eager_index.eager_children)
                # Query still works via JSON fallback.
                ids = {
                    m["id"] for m in fg.query("alpha", limit=10)["matches"]
                }
                self.assertIn("alpha\x1fservice:alpha", ids)
            finally:
                fg.close()

    def test_eager_handle_isinstance_check(self) -> None:
        """A sqlite-fresh child still loads as ``SqliteBackedGraph``.

        The eager path is an additional optimisation layer; it does not
        replace the per-child handle the federation cache holds.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[("alpha", _child_payload("alpha"), True)],
            )
            fg = FederatedGraph(root, eager_index=True)
            try:
                self.assertIsInstance(
                    fg._load_child("alpha"), SqliteBackedGraph,
                )
            finally:
                fg.close()


if __name__ == "__main__":
    unittest.main()
