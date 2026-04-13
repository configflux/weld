"""Tests for the boundary and entrypoint extraction strategy."""

from __future__ import annotations

import tempfile
from pathlib import Path

from cortex.strategies._helpers import StrategyResult
from cortex.strategies.boundary_entrypoint import extract

class TestEntrypointDetection:
    """Entrypoint nodes from if __name__ == '__main__' and CLI patterns."""

    def test_detects_main_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "services" / "api"
            pkg.mkdir(parents=True)
            main_py = pkg / "main.py"
            main_py.write_text('''\
"""API service entrypoint."""

import uvicorn
from app import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
''')
            source = {"glob": "services/api/*.py"}
            result = extract(root, source, {})

            assert isinstance(result, StrategyResult)
            entrypoint_nodes = {
                k: v for k, v in result.nodes.items()
                if v["type"] == "entrypoint"
            }
            assert len(entrypoint_nodes) == 1
            nid = list(entrypoint_nodes.keys())[0]
            assert nid == "entrypoint:services/api/main"
            node = entrypoint_nodes[nid]
            assert node["label"] == "main"
            assert node["props"]["file"] == "services/api/main.py"
            assert node["props"]["kind"] == "main_guard"

    def test_detects_cli_entry_click(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "tools"
            pkg.mkdir()
            cli_py = pkg / "deploy.py"
            cli_py.write_text('''\
"""Deploy CLI tool."""

import click

@click.command()
def main():
    """Run deployment."""
    pass

if __name__ == "__main__":
    main()
''')
            source = {"glob": "tools/*.py"}
            result = extract(root, source, {})

            entrypoint_nodes = {
                k: v for k, v in result.nodes.items()
                if v["type"] == "entrypoint"
            }
            assert len(entrypoint_nodes) == 1
            node = list(entrypoint_nodes.values())[0]
            assert node["props"]["kind"] == "cli"
            assert node["props"]["framework"] == "click"

    def test_detects_cli_entry_argparse(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "tools"
            pkg.mkdir()
            cli_py = pkg / "migrate.py"
            cli_py.write_text('''\
"""Migration tool."""

import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

if __name__ == "__main__":
    main()
''')
            source = {"glob": "tools/*.py"}
            result = extract(root, source, {})

            entrypoint_nodes = {
                k: v for k, v in result.nodes.items()
                if v["type"] == "entrypoint"
            }
            assert len(entrypoint_nodes) == 1
            node = list(entrypoint_nodes.values())[0]
            assert node["props"]["kind"] == "cli"
            assert node["props"]["framework"] == "argparse"

    def test_detects_uvicorn_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "services" / "api"
            pkg.mkdir(parents=True)
            main_py = pkg / "server.py"
            main_py.write_text('''\
"""API server."""

import uvicorn
from fastapi import FastAPI

app = FastAPI()

if __name__ == "__main__":
    uvicorn.run(app)
''')
            source = {"glob": "services/api/*.py"}
            result = extract(root, source, {})

            entrypoint_nodes = {
                k: v for k, v in result.nodes.items()
                if v["type"] == "entrypoint"
            }
            assert len(entrypoint_nodes) == 1
            node = list(entrypoint_nodes.values())[0]
            assert node["props"]["kind"] == "server"
            assert node["props"]["framework"] == "uvicorn"

    def test_no_entrypoint_without_main_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "lib"
            pkg.mkdir()
            lib_py = pkg / "utils.py"
            lib_py.write_text('''\
"""Utility functions."""

def helper():
    return 42
''')
            source = {"glob": "lib/*.py"}
            result = extract(root, source, {})

            entrypoint_nodes = {
                k: v for k, v in result.nodes.items()
                if v["type"] == "entrypoint"
            }
            assert len(entrypoint_nodes) == 0

class TestBoundaryDetection:
    """Boundary nodes from FastAPI app factories and ASGI/WSGI patterns."""

    def test_detects_fastapi_app_factory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "services" / "api"
            pkg.mkdir(parents=True)
            app_py = pkg / "app.py"
            app_py.write_text('''\
"""API application factory."""

from fastapi import FastAPI

def create_app() -> FastAPI:
    app = FastAPI(title="My API")
    return app

app = FastAPI()
''')
            source = {"glob": "services/api/*.py"}
            result = extract(root, source, {})

            boundary_nodes = {
                k: v for k, v in result.nodes.items()
                if v["type"] == "boundary"
            }
            assert len(boundary_nodes) == 1
            node = list(boundary_nodes.values())[0]
            assert node["props"]["kind"] == "api_surface"
            assert node["props"]["framework"] == "fastapi"

    def test_detects_flask_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "services" / "web"
            pkg.mkdir(parents=True)
            app_py = pkg / "app.py"
            app_py.write_text('''\
"""Web application."""

from flask import Flask

app = Flask(__name__)
''')
            source = {"glob": "services/web/*.py"}
            result = extract(root, source, {})

            boundary_nodes = {
                k: v for k, v in result.nodes.items()
                if v["type"] == "boundary"
            }
            assert len(boundary_nodes) == 1
            node = list(boundary_nodes.values())[0]
            assert node["props"]["kind"] == "api_surface"
            assert node["props"]["framework"] == "flask"

    def test_no_boundary_for_plain_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "lib"
            pkg.mkdir()
            util_py = pkg / "helpers.py"
            util_py.write_text('''\
"""Internal helpers."""

def compute():
    return 1 + 1
''')
            source = {"glob": "lib/*.py"}
            result = extract(root, source, {})

            boundary_nodes = {
                k: v for k, v in result.nodes.items()
                if v["type"] == "boundary"
            }
            assert len(boundary_nodes) == 0

class TestMetadataContract:
    """Every node/edge must satisfy the normalized metadata contract."""

    def test_node_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "svc"
            pkg.mkdir()
            main_py = pkg / "main.py"
            main_py.write_text('''\
if __name__ == "__main__":
    pass
''')
            source = {"glob": "svc/*.py"}
            result = extract(root, source, {})

            for nid, node in result.nodes.items():
                props = node["props"]
                assert props["source_strategy"] == "boundary_entrypoint"
                assert props["authority"] in ("canonical", "derived")
                assert props["confidence"] in ("definite", "inferred")
                assert isinstance(props["roles"], list)
                assert len(props["roles"]) > 0

    def test_edge_metadata(self) -> None:
        """Edges linking entrypoints to services must have proper metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "services" / "api"
            pkg.mkdir(parents=True)
            app_py = pkg / "app.py"
            app_py.write_text('''\
from fastapi import FastAPI
app = FastAPI()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app)
''')
            source = {"glob": "services/api/*.py"}
            result = extract(root, source, {})

            for edge in result.edges:
                assert "source_strategy" in edge["props"]
                assert edge["props"]["source_strategy"] == "boundary_entrypoint"
                assert "confidence" in edge["props"]

class TestRecursiveGlob:
    """Strategy handles recursive globs."""

    def test_recursive_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            deep = root / "a" / "b"
            deep.mkdir(parents=True)
            (deep / "main.py").write_text('if __name__ == "__main__":\n    pass\n')
            (root / "a" / "entry.py").write_text('if __name__ == "__main__":\n    pass\n')

            source = {"glob": "a/**/*.py"}
            result = extract(root, source, {})

            assert len(result.nodes) == 2

class TestDiscoveredFrom:
    """Strategy reports discovered_from paths."""

    def test_discovered_from_populated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "svc"
            pkg.mkdir()
            (pkg / "main.py").write_text('if __name__ == "__main__":\n    pass\n')

            source = {"glob": "svc/*.py"}
            result = extract(root, source, {})

            assert len(result.discovered_from) > 0

class TestExcludes:
    """Strategy respects exclude patterns."""

    def test_excludes_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "svc"
            pkg.mkdir()
            (pkg / "main.py").write_text('if __name__ == "__main__":\n    pass\n')
            (pkg / "test_main.py").write_text('if __name__ == "__main__":\n    pass\n')

            source = {"glob": "svc/*.py", "exclude": ["test_*"]}
            result = extract(root, source, {})

            assert len(result.nodes) == 1
            nid = list(result.nodes.keys())[0]
            assert "test_main" not in nid
