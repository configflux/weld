"""Terminal safety for the ``wd agents`` surface (hostile repository).

The Agent Graph is built entirely out of material the scanned repository
chooses: ``.claude/`` filenames, YAML frontmatter, descriptions, declared
render targets. A repository that names an agent
``planner<ESC>[2J.md`` -- or puts a screen-clear inside a ``description:`` --
gets those bytes into asset names, node ids and finding titles.

This surface previously built its output from a run of bare ``print()``
calls, so there was no single expression to escape and it sat outside the
write-boundary contract the rest of the CLI follows. It now renders through
``format_*`` functions with one sanitized write per command; this suite is
the behavioral proof, exercised end to end through ``wd`` rather than
against the formatters, so a future refactor that reintroduces a raw
``print`` fails here and not only in the lint.
"""

from __future__ import annotations

import io
import os
import tempfile
import textwrap
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Iterator

from weld.cli import main as wd_main

ESC = "\x1b"
CSI = "\x9b"
CLEAR = f"{ESC}[2J"

#: A filename only a hostile repo would produce. POSIX forbids just "/" and
#: NUL in a path component, so ESC is perfectly legal on disk.
HOSTILE_STEM = f"planner{CLEAR}"


@contextmanager
def _cwd(path: Path) -> Iterator[None]:
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _run(argv: list[str], root: Path) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with _cwd(root), redirect_stdout(out), redirect_stderr(err):
        try:
            rc = wd_main(argv)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


def _has_raw_control(text: str) -> bool:
    """True if *text* carries a byte a terminal would act on.

    TAB and LF are the rendered layout and are deliberately preserved by
    :func:`weld._safe_text.sanitize_terminal_text`, so they are not counted.
    """
    return any(
        ch not in "\t\n" and (ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F)
        for ch in text
    )


class HostileAgentRepoTest(unittest.TestCase):
    """Every ``wd agents`` render path escapes what the repo supplied."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        agents = self.root / ".claude" / "agents"
        agents.mkdir(parents=True)
        # Control bytes in three independent places: the filename, the
        # declared name, and the free-text description.
        (agents / f"{HOSTILE_STEM}.md").write_text(
            textwrap.dedent(
                f"""\
                ---
                name: planner{CLEAR}
                description: Plans {CSI}2K changes.
                ---
                Body referencing @{HOSTILE_STEM}.
                """
            ),
            encoding="utf-8",
        )
        (agents / "clean.md").write_text(
            textwrap.dedent(
                """\
                ---
                name: clean
                description: An ordinary agent.
                ---
                """
            ),
            encoding="utf-8",
        )
        rc, _out, _err = _run(["agents", "discover"], self.root)
        self.assertIn(rc, (0, 1), "discover should complete, clean or not")

    def _assert_safe(self, argv: list[str]) -> str:
        _rc, out, err = _run(argv, self.root)
        stream = out + err
        self.assertTrue(stream.strip(), f"no output from wd {' '.join(argv)}")
        self.assertFalse(
            _has_raw_control(stream),
            f"raw control byte in `wd {' '.join(argv)}`: {stream[:400]!r}",
        )
        return stream

    def test_discover_summary_is_escaped(self):
        stream = self._assert_safe(["agents", "discover", "--show-diagnostics"])
        self.assertIn("Agent Graph discovery", stream)

    def test_list_is_escaped_and_shows_the_escape(self):
        stream = self._assert_safe(["agents", "list"])
        # Escaped, not stripped: the operator can still see what the id is.
        self.assertIn("\\x1b[2J", stream)

    def test_explain_is_escaped(self):
        self._assert_safe(["agents", "explain", "clean"])

    def test_explain_not_found_echoes_the_query_safely(self):
        # The miss path renders the caller-supplied asset back to stderr.
        _rc, out, err = _run(
            ["agents", "explain", f"missing{CLEAR}"], self.root,
        )
        self.assertFalse(_has_raw_control(out + err), repr((out + err)[:400]))

    def test_impact_is_escaped(self):
        self._assert_safe(["agents", "impact", "clean"])

    def test_audit_is_escaped(self):
        self._assert_safe(["agents", "audit"])

    def test_plan_change_is_escaped(self):
        self._assert_safe(["agents", "plan-change", f"touch {CLEAR} things"])

    def test_render_dry_run_is_escaped(self):
        self._assert_safe(["agents", "render"])

    def test_json_surfaces_carry_no_raw_control_byte(self):
        for argv in (
            ["agents", "list", "--json"],
            ["agents", "audit", "--json"],
            ["agents", "render", "--json"],
        ):
            with self.subTest(argv=argv):
                _rc, out, err = _run(argv, self.root)
                self.assertTrue(out.strip(), f"no output from {argv}")
                self.assertFalse(
                    _has_raw_control(out + err), repr((out + err)[:400]),
                )


class CleanRepoIsUnchangedTest(unittest.TestCase):
    """The escape must be invisible when there is nothing to escape.

    The refactor that introduced the write boundary rebuilt these blocks from
    ``print()`` runs into formatters; this pins that ordinary output did not
    shift a byte in the process.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        agents = self.root / ".claude" / "agents"
        agents.mkdir(parents=True)
        for name in ("alpha", "beta"):
            (agents / f"{name}.md").write_text(
                textwrap.dedent(
                    f"""\
                    ---
                    name: {name}
                    description: The {name} agent.
                    ---
                    """
                ),
                encoding="utf-8",
            )
        _run(["agents", "discover"], self.root)

    def test_list_output_shape_is_intact(self):
        _rc, out, _err = _run(["agents", "list"], self.root)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)
        self.assertNotIn("\\x", out)

    def test_discover_summary_shape_is_intact(self):
        _rc, out, _err = _run(["agents", "discover"], self.root)
        for expected in ("Agent Graph discovery", "Root:", "Assets:",
                         "Nodes:", "Edges:", "Diagnostics:", "Write:"):
            self.assertIn(expected, out)

    def test_explain_shape_is_intact(self):
        _rc, out, _err = _run(["agents", "explain", "alpha"], self.root)
        for expected in ("Type:", "Status:", "Platforms:", "Purpose:",
                         "Source files:", "Related:", "Potential overlap:"):
            self.assertIn(expected, out)


if __name__ == "__main__":
    unittest.main()
