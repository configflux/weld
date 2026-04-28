"""Polyrepo integration tests for :func:`weld._telemetry.resolve_path`.

ADR 0035 § "Local-only JSONL writer at a polyrepo-aware path" requires
that, when a user runs ``wd`` anywhere inside a polyrepo workspace, the
resulting telemetry event lands in ``<workspace_root>/.weld/telemetry.jsonl``
-- never in a child repo's ``.weld/`` directory. These tests build a real
fixture (workspace root + two child git repos, mirroring the pattern
from ``weld_workspace_state_test.py``) and exercise :func:`resolve_path`
plus :class:`Recorder` end-to-end against it. The XDG-fallback case
redirects ``XDG_STATE_HOME``/``HOME`` into a tempdir so the real
``~/.local/state/weld/`` is never touched. Everything lives inside
``tempfile.TemporaryDirectory()`` and is cleaned up on exit.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

# Make ``weld`` importable from a Bazel runfiles tree as well as from the
# repo root in local runs.
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import _telemetry as tel  # noqa: E402
from weld.workspace import (  # noqa: E402
    ChildEntry,
    WorkspaceConfig,
    dump_workspaces_yaml,
)


# --- Fixture helpers -------------------------------------------------------


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(repo_root: Path) -> Path:
    """Init a real git repo (mirrors ``weld_workspace_state_test.py``)."""
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _write_workspaces(root: Path, children: list[ChildEntry]) -> None:
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")


def _build_polyrepo(tmp: Path) -> tuple[Path, Path, Path]:
    """Build ``<tmp>/workspace_root`` with workspaces.yaml + 2 child git
    repos (each with their own ``.weld/discover.yaml``). Returns
    ``(root, child_a, child_b)``.
    """
    root = tmp / "workspace_root"
    weld = root / ".weld"
    weld.mkdir(parents=True, exist_ok=True)
    (weld / "discover.yaml").write_text("# placeholder\n", encoding="utf-8")

    child_a = _init_repo(root / "child-a")
    (child_a / ".weld").mkdir(parents=True, exist_ok=True)
    (child_a / ".weld" / "discover.yaml").write_text(
        "# placeholder\n", encoding="utf-8"
    )

    child_b = _init_repo(root / "child-b")
    (child_b / ".weld").mkdir(parents=True, exist_ok=True)
    (child_b / ".weld" / "discover.yaml").write_text(
        "# placeholder\n", encoding="utf-8"
    )

    _write_workspaces(
        root,
        [
            ChildEntry(name="child-a", path="child-a"),
            ChildEntry(name="child-b", path="child-b"),
        ],
    )
    return root, child_a, child_b


class _FakeClock:
    """Deterministic monotonic_ns substitute for stable ``duration_ms``."""

    def __init__(self, start_ns: int = 1_000_000_000) -> None:
        self.t_ns = start_ns

    def __call__(self) -> int:
        return self.t_ns

    def advance_ms(self, delta_ms: int) -> None:
        self.t_ns += delta_ms * 1_000_000


@contextmanager
def _bounded_walk_up(boundary: Path):
    """Stop ``_walk_up`` from escaping above ``boundary`` so a stray
    ``.weld/`` above the OS tempdir (e.g., the host repo when running
    outside a Bazel sandbox) cannot mask the XDG fallback under test.
    """
    boundary_resolved = boundary.resolve()
    real_walk_up = tel._walk_up

    def bounded(start: Path):
        for parent in real_walk_up(start):
            yield parent
            try:
                if parent.resolve() == boundary_resolved:
                    return
            except OSError:
                return

    with mock.patch.object(tel, "_walk_up", side_effect=bounded):
        yield


def _expected_root_telemetry(root: Path) -> Path:
    return root / ".weld" / tel.TELEMETRY_FILENAME


@contextmanager
def _clean_telemetry_env():
    """Drop ``WELD_TELEMETRY`` from the environment so an inherited
    opt-out value cannot silence the Recorder during tests that assert
    events were written."""
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("WELD_TELEMETRY", None)
        yield


# --- Tests -----------------------------------------------------------------


class ResolvePathFromWorkspaceRootTests(unittest.TestCase):
    """ADR 0035 § "Local-only JSONL writer": polyrepo workspace wins."""

    def test_resolve_from_workspace_root_returns_root_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, _ca, _cb = _build_polyrepo(Path(tmp))
            self.assertEqual(
                tel.resolve_path(root),
                _expected_root_telemetry(root),
            )

    def test_resolve_from_child_a_returns_root_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, child_a, _cb = _build_polyrepo(Path(tmp))
            self.assertEqual(
                tel.resolve_path(child_a),
                _expected_root_telemetry(root),
            )

    def test_resolve_from_child_b_returns_root_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, _ca, child_b = _build_polyrepo(Path(tmp))
            self.assertEqual(
                tel.resolve_path(child_b),
                _expected_root_telemetry(root),
            )

    def test_resolve_from_nested_dir_in_child_returns_root_file(self) -> None:
        """Walking up from a sub-directory inside a child must still
        land on the workspace root, never on the child's ``.weld/``."""
        with tempfile.TemporaryDirectory() as tmp:
            root, child_a, _cb = _build_polyrepo(Path(tmp))
            nested = child_a / "src" / "pkg"
            nested.mkdir(parents=True, exist_ok=True)
            self.assertEqual(
                tel.resolve_path(nested),
                _expected_root_telemetry(root),
            )


class RecorderWritesToWorkspaceRootTests(unittest.TestCase):
    """ADR 0035: a Recorder run from a child writes to the workspace
    root and never creates a child-side telemetry file."""

    def test_recorder_from_child_a_writes_only_to_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, _clean_telemetry_env():
            root, child_a, child_b = _build_polyrepo(Path(tmp))
            clock = _FakeClock()

            # ``root`` argument to Recorder is the cwd of the invocation
            # in production (cli.py passes ``Path.cwd()``); we model the
            # "user runs wd from child-a" case.
            with tel.Recorder(
                surface="cli",
                command="discover",
                flags=[],
                root=child_a,
                clock=clock,
                stderr=io.StringIO(),
            ):
                clock.advance_ms(7)

            root_file = _expected_root_telemetry(root)
            self.assertTrue(
                root_file.is_file(),
                f"expected telemetry at {root_file}",
            )
            with root_file.open("r", encoding="utf-8") as fh:
                lines = [ln for ln in fh if ln.strip()]
            self.assertEqual(len(lines), 1)

            # Negative: child .weld/ directories must stay free of any
            # telemetry artefact, including the disabled sentinel and
            # any rotation tmp file.
            self.assertFalse(
                (child_a / ".weld" / tel.TELEMETRY_FILENAME).exists()
            )
            self.assertFalse(
                (child_b / ".weld" / tel.TELEMETRY_FILENAME).exists()
            )

    def test_two_recorders_from_different_children_share_one_file(self) -> None:
        """The shareability promise: events from any cwd inside the
        workspace converge on a single artefact -- no per-child files."""
        with tempfile.TemporaryDirectory() as tmp, _clean_telemetry_env():
            root, child_a, child_b = _build_polyrepo(Path(tmp))
            stderr = io.StringIO()

            with tel.Recorder(
                surface="cli",
                command="discover",
                flags=[],
                root=child_a,
                clock=_FakeClock(),
                stderr=stderr,
            ):
                pass
            with tel.Recorder(
                surface="cli",
                command="query",
                flags=[],
                root=child_b,
                clock=_FakeClock(),
                stderr=stderr,
            ):
                pass

            root_file = _expected_root_telemetry(root)
            with root_file.open("r", encoding="utf-8") as fh:
                lines = [ln for ln in fh if ln.strip()]
            self.assertEqual(len(lines), 2)
            self.assertFalse(
                (child_a / ".weld" / tel.TELEMETRY_FILENAME).exists()
            )
            self.assertFalse(
                (child_b / ".weld" / tel.TELEMETRY_FILENAME).exists()
            )


class XdgFallbackOutsideAnyProjectTests(unittest.TestCase):
    """ADR 0035 § "No project context": fall back to XDG state path."""

    def test_resolve_returns_xdg_state_when_no_project_in_walk(self) -> None:
        with tempfile.TemporaryDirectory() as outer:
            outer_path = Path(outer)
            # Path with no .weld/ in any ancestor up to the boundary.
            elsewhere = outer_path / "elsewhere"
            elsewhere.mkdir(parents=True, exist_ok=True)
            xdg_home = outer_path / "_xdg"

            with mock.patch.dict(
                os.environ,
                {"XDG_STATE_HOME": str(xdg_home)},
                clear=False,
            ), _bounded_walk_up(outer_path):
                resolved = tel.resolve_path(elsewhere)

            self.assertIsNotNone(resolved)
            self.assertEqual(
                resolved,
                xdg_home / "weld" / tel.TELEMETRY_FILENAME,
            )

    def test_xdg_fallback_uses_home_when_env_unset(self) -> None:
        """When ``XDG_STATE_HOME`` is unset, the fallback derives from
        ``Path.home()``. We redirect ``HOME`` into a tempdir to keep
        the assertion deterministic and to keep the real
        ``~/.local/state/weld/`` untouched."""
        with tempfile.TemporaryDirectory() as outer:
            outer_path = Path(outer)
            elsewhere = outer_path / "elsewhere"
            elsewhere.mkdir(parents=True, exist_ok=True)
            fake_home = outer_path / "fake_home"
            fake_home.mkdir(parents=True, exist_ok=True)

            env_overrides = {"HOME": str(fake_home)}
            # Drop XDG_STATE_HOME so the home-based branch executes.
            with mock.patch.dict(
                os.environ, env_overrides, clear=False
            ), _bounded_walk_up(outer_path):
                os.environ.pop("XDG_STATE_HOME", None)
                resolved = tel.resolve_path(elsewhere)

            self.assertIsNotNone(resolved)
            self.assertEqual(
                resolved,
                fake_home / ".local" / "state" / "weld" / tel.TELEMETRY_FILENAME,
            )


class SingleRepoRegressionTests(unittest.TestCase):
    """ADR 0035 tier 2: a project with no ``workspaces.yaml`` still
    resolves to its own ``.weld/telemetry.jsonl`` (single-repo mode)."""

    def test_single_repo_root_with_only_discover_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            (root / ".weld" / "discover.yaml").write_text(
                "# placeholder\n", encoding="utf-8"
            )
            self.assertEqual(
                tel.resolve_path(root),
                _expected_root_telemetry(root),
            )

    def test_single_repo_does_not_match_workspace_tier(self) -> None:
        """Without ``workspaces.yaml`` the polyrepo tier must not fire
        even when child-like sub-directories contain ``.weld/``."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            (root / ".weld" / "discover.yaml").write_text(
                "# placeholder\n", encoding="utf-8"
            )
            nested = root / "sub" / "pkg"
            nested.mkdir(parents=True, exist_ok=True)
            self.assertEqual(
                tel.resolve_path(nested),
                _expected_root_telemetry(root),
            )


class ChildTelemetryNeverMaterialisesTests(unittest.TestCase):
    """ADR 0035 invariant: child ``.weld/telemetry.jsonl`` is never
    created, even after multiple Recorder runs from inside the child."""

    def test_repeated_recorder_from_child_does_not_create_child_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, _clean_telemetry_env():
            root, child_a, child_b = _build_polyrepo(Path(tmp))
            stderr = io.StringIO()
            for _ in range(3):
                with tel.Recorder(
                    surface="cli",
                    command="discover",
                    flags=[],
                    root=child_a,
                    clock=_FakeClock(),
                    stderr=stderr,
                ):
                    pass

            root_file = _expected_root_telemetry(root)
            self.assertTrue(root_file.is_file())
            self.assertFalse(
                (child_a / ".weld" / tel.TELEMETRY_FILENAME).exists()
            )
            self.assertFalse(
                (child_b / ".weld" / tel.TELEMETRY_FILENAME).exists()
            )
            # The root file should hold exactly the events we wrote.
            with root_file.open("r", encoding="utf-8") as fh:
                self.assertEqual(
                    sum(1 for ln in fh if ln.strip()),
                    3,
                )


if __name__ == "__main__":
    unittest.main()
