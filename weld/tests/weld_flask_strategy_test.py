"""Tests for the Flask discovery strategy (bd 778o).

The strategy walks Python modules and emits one ``route:<METHOD>:<path>``
node per decorator/method pair and per ``add_url_rule`` call, plus a
minimal handler ``symbol:py:<module>:<qualname>`` node and an
``exposes`` edge from the handler symbol to the route. The shape
mirrors the C# ``csharp_aspnet_routes`` controller -> route edge that
tier-check criterion 3 reads via ``check_flask`` in
:mod:`tools._tier_check_framework_python`.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.strategies._helpers import StrategyResult  # noqa: E402
from weld.strategies.flask import extract  # noqa: E402


def _write(pkg: Path, name: str, body: str) -> None:
    (pkg / name).write_text(textwrap.dedent(body))


class TestFlaskMissingAndEmpty(unittest.TestCase):
    """Defensive cases: missing directory, no flask imports, syntax errors."""

    def test_missing_glob_parent_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = extract(root, {"glob": "app/*.py"}, {})
            self.assertIsInstance(result, StrategyResult)
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_module_without_flask_import_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            _write(root / "app", "main.py", """\
                # No flask import, even if it defines a route() lookalike.
                class Pretend:
                    def route(self, _p):
                        def deco(f):
                            return f
                        return deco
                app = Pretend()
                @app.route("/x")
                def x():
                    return ""
            """)
            result = extract(root, {"glob": "app/*.py"}, {})
            self.assertEqual(result.nodes, {})

    def test_syntax_error_module_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            _write(root / "app", "broken.py", "class Broken(:\n")
            result = extract(root, {"glob": "app/*.py"}, {})
            self.assertEqual(result.nodes, {})


class TestFlaskDecoratorRoutes(unittest.TestCase):
    """``@app.route('/path')`` and ``@bp.route('/path')`` emission."""

    def test_app_route_emits_route_and_exposes_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            _write(root / "app", "main.py", """\
                from flask import Flask
                app = Flask(__name__)
                @app.route("/hello")
                def hello():
                    return "hi"
            """)
            result = extract(root, {"glob": "app/*.py"}, {})
            self.assertIn("route:GET:/hello", result.nodes)
            route_node = result.nodes["route:GET:/hello"]
            self.assertEqual(route_node["type"], "route")
            self.assertEqual(
                route_node["props"]["source_strategy"], "flask",
            )
            self.assertEqual(
                route_node["props"]["route_source"], "decorator",
            )
            self.assertEqual(
                route_node["props"]["function"], "hello",
            )
            # Handler symbol must exist for the exposes edge to survive
            # the dangling-edge post-pass.
            handler_id = "symbol:py:app.main:hello"
            self.assertIn(handler_id, result.nodes)
            exposes = [
                e for e in result.edges
                if e["type"] == "exposes"
                and e["from"] == handler_id
                and e["to"] == "route:GET:/hello"
            ]
            self.assertEqual(len(exposes), 1, result.edges)
            self.assertEqual(
                exposes[0]["props"]["source_strategy"], "flask",
            )

    def test_blueprint_route_emits_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            _write(root / "app", "api.py", """\
                from flask import Blueprint
                bp = Blueprint("api", __name__)
                @bp.route("/ping")
                def ping():
                    return "pong"
            """)
            result = extract(root, {"glob": "app/*.py"}, {})
            self.assertIn("route:GET:/ping", result.nodes)
            handler_id = "symbol:py:app.api:ping"
            exposes = [
                e for e in result.edges
                if e["type"] == "exposes"
                and e["from"] == handler_id
            ]
            self.assertEqual(len(exposes), 1, result.edges)

    def test_methods_kwarg_expands_per_verb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            _write(root / "app", "main.py", """\
                from flask import Flask
                app = Flask(__name__)
                @app.route("/things", methods=["GET", "POST"])
                def things():
                    return ""
            """)
            result = extract(root, {"glob": "app/*.py"}, {})
            self.assertIn("route:GET:/things", result.nodes)
            self.assertIn("route:POST:/things", result.nodes)
            handler_id = "symbol:py:app.main:things"
            exposes = [
                e for e in result.edges
                if e["type"] == "exposes" and e["from"] == handler_id
            ]
            # Both routes share the same handler -> two exposes edges.
            self.assertEqual(len(exposes), 2, result.edges)

    def test_non_literal_methods_falls_back_to_get(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            _write(root / "app", "main.py", """\
                from flask import Flask
                METHODS = ["GET", "POST"]
                app = Flask(__name__)
                @app.route("/dynamic", methods=METHODS)
                def dynamic():
                    return ""
            """)
            result = extract(root, {"glob": "app/*.py"}, {})
            # The non-literal kwarg is ignored; we fall back to GET.
            self.assertIn("route:GET:/dynamic", result.nodes)
            self.assertNotIn("route:POST:/dynamic", result.nodes)

    def test_unknown_methods_are_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            _write(root / "app", "main.py", """\
                from flask import Flask
                app = Flask(__name__)
                @app.route("/odd", methods=["GET", "BOGUS"])
                def odd():
                    return ""
            """)
            result = extract(root, {"glob": "app/*.py"}, {})
            self.assertIn("route:GET:/odd", result.nodes)
            self.assertNotIn("route:BOGUS:/odd", result.nodes)


class TestFlaskAddUrlRule(unittest.TestCase):
    """``app.add_url_rule(...)`` emission with view_func resolution."""

    def test_add_url_rule_with_view_func_emits_exposes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            _write(root / "app", "main.py", """\
                from flask import Flask
                app = Flask(__name__)
                def status():
                    return "ok"
                app.add_url_rule("/status", view_func=status, methods=["GET"])
            """)
            result = extract(root, {"glob": "app/*.py"}, {})
            self.assertIn("route:GET:/status", result.nodes)
            route_node = result.nodes["route:GET:/status"]
            self.assertEqual(
                route_node["props"]["route_source"], "add_url_rule",
            )
            handler_id = "symbol:py:app.main:status"
            self.assertIn(handler_id, result.nodes)
            exposes = [
                e for e in result.edges
                if e["type"] == "exposes"
                and e["from"] == handler_id
                and e["to"] == "route:GET:/status"
            ]
            self.assertEqual(len(exposes), 1, result.edges)

    def test_add_url_rule_without_view_func_emits_route_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            _write(root / "app", "main.py", """\
                from flask import Flask
                app = Flask(__name__)
                app.add_url_rule("/anon", endpoint="anon")
            """)
            result = extract(root, {"glob": "app/*.py"}, {})
            self.assertIn("route:GET:/anon", result.nodes)
            exposes = [
                e for e in result.edges
                if e["type"] == "exposes"
                and e["to"] == "route:GET:/anon"
            ]
            # No view_func -> no static handler to expose from.
            self.assertEqual(exposes, [])


class TestFlaskDoubleStarGlob(unittest.TestCase):
    """Recursive ``**/*.py`` glob is honoured."""

    def test_recursive_glob_walks_subdirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inner = root / "app" / "api"
            inner.mkdir(parents=True)
            _write(inner, "v1.py", """\
                from flask import Blueprint
                bp = Blueprint("v1", __name__, url_prefix="/v1")
                @bp.route("/health")
                def health():
                    return "ok"
            """)
            result = extract(root, {"glob": "**/*.py"}, {})
            self.assertIn("route:GET:/health", result.nodes)
            self.assertIn("symbol:py:app.api.v1:health", result.nodes)


if __name__ == "__main__":
    unittest.main()
