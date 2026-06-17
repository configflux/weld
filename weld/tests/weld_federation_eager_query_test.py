"""Eager federation inverted-index aggregation (ADR 0063).

Covers the ADR 0063 default-on amendment: the eager path is the
default for fresh-sidecar children, stale/missing-sidecar children
keep the lazy fallback, and ``WELD_FEDERATION_EAGER`` is a two-way
override (truthy forces on, falsy force-disables). Match-set parity
with the lazy path is the correctness contract.
"""

from __future__ import annotations

import contextlib
import os
import unittest
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory


from weld._sqlite_reader import SqliteBackedGraph  # noqa: E402
from weld.federation import FederatedGraph  # noqa: E402
from weld.tests._federation_sqlite_fixtures import (  # noqa: E402
    graph_payload,
    make_workspace,
)


@contextlib.contextmanager
def _eager_env(value: str | None) -> Iterator[None]:
    """Set/clear ``WELD_FEDERATION_EAGER`` for a block, then restore it.

    ``value=None`` unsets the variable so the constructor sees the bare
    default; any string sets it. The prior value is always restored so
    one test cannot leak the env into the next.
    """
    old = os.environ.get("WELD_FEDERATION_EAGER")
    if value is None:
        os.environ.pop("WELD_FEDERATION_EAGER", None)
    else:
        os.environ["WELD_FEDERATION_EAGER"] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("WELD_FEDERATION_EAGER", None)
        else:
            os.environ["WELD_FEDERATION_EAGER"] = old


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
    """ADR 0063 default-on amendment: eager aggregation is the default
    for fresh-sidecar children; the env var force-disables it."""

    def test_eager_on_by_default(self) -> None:
        """Constructor with no flag and no env var builds the eager index.

        ADR 0063 default-on amendment (AC #1): a fresh-sidecar child is
        covered by the eager index without any opt-in. The env var is
        cleared for the duration so an ambient force-disable in the
        calling environment cannot mask the default.
        """
        with TemporaryDirectory() as tmp, _eager_env(None):
            root = Path(tmp)
            make_workspace(
                root,
                children=[("alpha", _child_payload("alpha"), True)],
            )
            fg = FederatedGraph(root)
            try:
                self.assertTrue(fg.eager_index_active)
                self.assertIn("alpha", fg._eager_index.eager_children)
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

    def test_eager_env_var_force_disable(self) -> None:
        """``WELD_FEDERATION_EAGER=0`` force-disables with no constructor arg.

        ADR 0063 default-on amendment (AC #3): the env var must still be
        able to turn the eager path off even for a fresh-sidecar child.
        """
        with TemporaryDirectory() as tmp, _eager_env("0"):
            root = Path(tmp)
            make_workspace(
                root,
                children=[("alpha", _child_payload("alpha"), True)],
            )
            fg = FederatedGraph(root)
            try:
                self.assertFalse(fg.eager_index_active)
                self.assertEqual(set(), fg._eager_index.eager_children)
            finally:
                fg.close()

    def test_eager_env_var_two_way_override(self) -> None:
        """Documented falsy values force off; truthy and the default-fall
        cases (empty / unrecognized) are on (ADR 0063 default-on, AC #3)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[("alpha", _child_payload("alpha"), True)],
            )
            for value, expected in (
                # Documented truthy -> on.
                ("1", True), ("true", True), ("True", True),
                ("yes", True), ("YES", True), ("on", True), ("ON", True),
                # Documented falsy -> force-disable.
                ("0", False), ("false", False), ("False", False),
                ("no", False), ("NO", False), ("off", False), ("OFF", False),
                # Empty / unrecognized -> default (on).
                ("", True), ("anything-else", True),
            ):
                with self.subTest(value=value), _eager_env(value):
                    fg = FederatedGraph(root)
                    try:
                        self.assertEqual(
                            fg.eager_index_active, expected,
                            f"WELD_FEDERATION_EAGER={value!r} should yield {expected}",
                        )
                    finally:
                        fg.close()

    def test_eager_constructor_arg_overrides_env(self) -> None:
        """Explicit ``eager_index=False`` wins over a truthy env var."""
        with TemporaryDirectory() as tmp, _eager_env("1"):
            root = Path(tmp)
            make_workspace(
                root,
                children=[("alpha", _child_payload("alpha"), True)],
            )
            fg = FederatedGraph(root, eager_index=False)
            try:
                self.assertFalse(fg.eager_index_active)
            finally:
                fg.close()

    def test_eager_default_matches_lazy_set(self) -> None:
        """Default-on (no kwarg, no env) match set == explicit lazy set.

        ADR 0063 default-on amendment (AC #1): the federation served with
        the new default produces byte-identical ``matches`` to an
        explicit ``eager_index=False`` lazy federation. Proves the
        default flip changed the path, not the results.
        """
        with TemporaryDirectory() as tmp, _eager_env(None):
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
                "alpha_helper",
                "service alpha",
                "no_match_term_xyz",
            ):
                with self.subTest(term=term):
                    fg_default = FederatedGraph(root)
                    try:
                        self.assertTrue(fg_default.eager_index_active)
                        default_ids = {
                            m["id"]
                            for m in fg_default.query(term, limit=50)["matches"]
                        }
                    finally:
                        fg_default.close()

                    fg_lazy = FederatedGraph(root, eager_index=False)
                    try:
                        lazy_ids = {
                            m["id"]
                            for m in fg_lazy.query(term, limit=50)["matches"]
                        }
                    finally:
                        fg_lazy.close()

                    self.assertEqual(
                        default_ids, lazy_ids,
                        f"term={term!r} default-on vs lazy match sets differ:"
                        f" only_default={default_ids - lazy_ids}"
                        f" only_lazy={lazy_ids - default_ids}",
                    )

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

    def test_default_on_mixed_fresh_and_stale(self) -> None:
        """Default-on covers the fresh child; the stale child falls back.

        ADR 0063 default-on amendment (AC #1 + AC #2): with no constructor
        arg and no env var, a fresh-sidecar child is served from the eager
        index while a stale-sidecar child keeps the lazy/JSON path -- and
        both return correct matches against the explicit-lazy baseline.
        """
        with TemporaryDirectory() as tmp, _eager_env(None):
            root = Path(tmp)
            make_workspace(
                root,
                children=[
                    ("alpha", _child_payload("alpha"), True),
                    ("beta", _child_payload("beta"), True),
                ],
            )
            # Make beta's sidecar stale by appending to its graph.json so
            # ``open_sidecar_if_fresh`` rejects it and the federation falls
            # back to the JSON-backed Graph for beta.
            beta_graph = root / "beta" / ".weld" / "graph.json"
            beta_graph.write_bytes(beta_graph.read_bytes() + b"\n")

            # Baseline: explicit lazy federation for match-set parity.
            fg_lazy = FederatedGraph(root, eager_index=False)
            try:
                lazy_service = {
                    m["id"] for m in fg_lazy.query("service", limit=20)["matches"]
                }
            finally:
                fg_lazy.close()

            fg = FederatedGraph(root)
            try:
                self.assertTrue(fg.eager_index_active)
                # Fresh child eager-covered; stale child explicitly not.
                self.assertIn("alpha", fg._eager_index.eager_children)
                self.assertNotIn("beta", fg._eager_index.eager_children)
                # The default-on federation returns the same match set as
                # the lazy baseline across both children (no regression).
                default_service = {
                    m["id"] for m in fg.query("service", limit=20)["matches"]
                }
                self.assertEqual(lazy_service, default_service)
                # Spot-check both children resolve a child-specific term.
                self.assertIn(
                    "alpha\x1fservice:alpha",
                    {m["id"] for m in fg.query("alpha", limit=10)["matches"]},
                )
                self.assertIn(
                    "beta\x1fservice:beta",
                    {m["id"] for m in fg.query("beta", limit=10)["matches"]},
                )
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
