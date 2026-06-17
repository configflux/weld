"""Integration tests: ``wd discover`` warns on empty config + real source.

A corpus with no ``.weld/discover.yaml`` (or one that resolves to zero
``sources``) but a tree full of recognized source files is almost always
an un-initialised checkout -- the operator forgot ``wd init``. The legacy
behaviour silently produced a 0-node graph and exited 0, which then let
``tools/tier_check.py`` report vacuous passes against nothing.
Discovery must instead surface a
loud ``[weld] warning:`` line naming ``wd init`` so the operator sees the
gap before they trust the empty graph.

The guard is intentionally conservative: it only fires when zero sources
are configured *and* the tree contains files whose extensions are
recognized by ``wd init``'s detector (``weld.init_detect``). A genuinely
empty or non-code directory stays silent so the warning means something.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from weld import discover as discover_mod  # noqa: E402

_WARN_NEEDLE = "no sources configured"


def _run_discover(root: Path) -> str:
    """Run ``wd discover`` against *root* and return captured stderr."""
    err = io.StringIO()
    out = io.StringIO()
    with redirect_stderr(err), redirect_stdout(out):
        rc = discover_mod.main(
            [str(root), "--quiet", "--no-enrich", "--no-sqlite"]
        )
    assert rc == 0, f"discover failed (rc={rc}): {err.getvalue()}"
    return err.getvalue()


def _warn_lines(stderr: str) -> list[str]:
    return [
        line
        for line in stderr.splitlines()
        if "[weld] warning:" in line and _WARN_NEEDLE in line
    ]


class DiscoverEmptySourcesWarningTest(unittest.TestCase):
    """No configured sources + recognized files -> one ``[weld] warning:``."""

    def test_no_config_with_recognized_source_warns(self) -> None:
        """A raw checkout (no discover.yaml) full of .go files warns."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.go").write_text(
                "package main\nfunc main() {}\n", encoding="utf-8"
            )
            (root / "server.go").write_text(
                "package main\ntype Server struct{}\n", encoding="utf-8"
            )
            stderr = _run_discover(root)

        lines = _warn_lines(stderr)
        self.assertEqual(
            len(lines),
            1,
            f"Expected exactly one no-sources warning, got {lines!r}\n"
            f"full stderr:\n{stderr}",
        )
        # The line must steer the operator at the recovery command.
        self.assertIn("wd init", lines[0])

    def test_empty_sources_config_with_recognized_source_warns(self) -> None:
        """An explicit ``sources: []`` config also trips the guard."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            (root / ".weld" / "discover.yaml").write_text(
                "sources: []\ntopology: {}\n", encoding="utf-8"
            )
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            stderr = _run_discover(root)

        self.assertEqual(
            len(_warn_lines(stderr)),
            1,
            f"Expected no-sources warning for empty sources config; "
            f"stderr:\n{stderr}",
        )

    def test_no_recognized_source_stays_silent(self) -> None:
        """An empty / non-code tree must NOT warn (no false alarm)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # README + license: nothing ``wd init`` would scaffold a
            # source entry for.
            (root / "README.md").write_text("# hi\n", encoding="utf-8")
            (root / "LICENSE").write_text("MIT\n", encoding="utf-8")
            stderr = _run_discover(root)

        self.assertEqual(
            _warn_lines(stderr),
            [],
            f"Did not expect a no-sources warning for a non-code tree; "
            f"stderr:\n{stderr}",
        )

    def test_configured_sources_stay_silent(self) -> None:
        """A properly-init'd corpus (sources present) must NOT warn."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            (root / ".weld" / "discover.yaml").write_text(
                "sources:\n"
                '  - glob: "**/*.py"\n'
                "    type: file\n"
                "    strategy: python_module\n",
                encoding="utf-8",
            )
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            stderr = _run_discover(root)

        self.assertEqual(
            _warn_lines(stderr),
            [],
            f"A configured corpus must not emit the no-sources warning; "
            f"stderr:\n{stderr}",
        )


if __name__ == "__main__":
    unittest.main()
