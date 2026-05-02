"""Shared test helpers for the telemetry test modules.

The four telemetry test files

    weld_telemetry_cli_test.py
    weld_telemetry_first_run_test.py
    weld_telemetry_optout_test.py
    weld_telemetry_cli_failure_test.py

each set up a tempdir-backed single-repo project, swap in captured
``stdout``/``stderr`` buffers, optionally redirect ``XDG_STATE_HOME``,
and chdir into the temp project. This module centralises those
fixtures so the tests stay focused on assertions.

Conventions intentionally match the rest of ``weld/tests``: helpers
live as a top-level module beside their consumers (see
``diff_fixtures.py``, ``cpp_resolver_fakes.py``, ``mcp_expected_tools.py``)
rather than as a ``conftest.py`` or a separate ``py_library`` target.
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest import mock


def make_repo(root: Path) -> Path:
    """Create a minimal single-repo project so ``resolve_path()`` finds it.

    Writes the ``.weld/`` directory and a placeholder ``discover.yaml`` so
    that any code path that walks up looking for a project root succeeds.
    Returns ``root`` unchanged for chaining.
    """
    weld = root / ".weld"
    weld.mkdir(parents=True, exist_ok=True)
    (weld / "discover.yaml").write_text("# placeholder\n")
    return root


@contextmanager
def chdir(target: Path) -> Iterator[None]:
    """Temporarily ``chdir`` into ``target`` and restore on exit."""
    prev = Path.cwd()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(prev)


@contextmanager
def noop_ctx() -> Iterator[None]:
    """A do-nothing context manager.

    Used by call sites that conditionally apply ``chdir`` only when a
    cwd is supplied (e.g. ``cli_test._run`` with ``cwd=None``).
    """
    yield


@contextmanager
def captured(
    root: Path,
    env: dict[str, str] | None = None,
) -> Iterator[tuple[io.StringIO, io.StringIO]]:
    """Run with isolated cwd, env (XDG redirected), captured streams.

    Parameters
    ----------
    root:
        The project root (a tempdir created via :func:`make_repo`). The
        helper chdirs into this directory and points ``XDG_STATE_HOME``
        at ``root/_xdg`` so first-run sentinels stay sandboxed.
    env:
        Optional extra env vars to overlay. ``XDG_STATE_HOME`` is
        always set; pass e.g. ``{"WELD_TELEMETRY": "off"}`` to force
        an opt-out tier. When ``env`` does not specify
        ``WELD_TELEMETRY``, any inherited value is dropped so default-on
        baseline behaviour is reproducible.

    Yields
    ------
    ``(out_buf, err_buf)``: the captured stdout/stderr buffers, so
    tests can read ``.getvalue()`` after the block exits.
    """
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    base_env: dict[str, str] = {"XDG_STATE_HOME": str(root / "_xdg")}
    if env:
        base_env.update(env)
    with chdir(root), \
            mock.patch.dict(os.environ, base_env, clear=False), \
            mock.patch.object(sys, "stdout", out_buf), \
            mock.patch.object(sys, "stderr", err_buf):
        if env is None or "WELD_TELEMETRY" not in env:
            os.environ.pop("WELD_TELEMETRY", None)
        yield out_buf, err_buf
