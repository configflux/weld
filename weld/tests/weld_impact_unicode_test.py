"""Unicode/quotePath coverage for ``weld.impact_cli._git_diff_files``.

Companion to ``weld_impact_cli_test.py``'s working-tree porcelain test
(which locks ``_git_status_files`` against unicode + literal ``" -> "``
filenames). That fix applied the same ``-c core.quotePath=false``
plus ``-z`` NUL-split treatment to **both** helpers, but the diff path
had no automated lock -- only a manual verification. A regression that
reverts to ``splitlines`` or drops the ``-c`` flag on the diff path
would silently mangle unicode filenames in ``--from-diff`` output.

This module is split out of ``weld_impact_cli_test.py`` because that
file is at the 400-line cap. It imports the shared
``_impact_test_helpers`` fixtures so the setup matches the sibling tests.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from weld.tests._impact_test_helpers import (
    ensure_repo_root_on_syspath,
    git,
    make_git_repo,
    unittest,
    write_graph,
)

ensure_repo_root_on_syspath()


class GitDiffFilesUnicodeTest(unittest.TestCase):
    """Lock ``_git_diff_files`` against unicode + ``" -> "`` filenames.

    Mirrors the existing working-tree test for ``_git_status_files``:
    creates a real git repo, commits a baseline, then commits two files
    with non-ASCII / quotePath-affected names and asserts the helper
    returns the exact UTF-8 strings rather than git's default C-quoted
    octal-escape form.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        make_git_repo(self.root)
        write_graph(self.root)

        # Baseline commit so HEAD~1 is well-defined for the diff.
        (self.root / "weld").mkdir(exist_ok=True)
        (self.root / "weld" / "graph.py").write_text(
            "# baseline\n", encoding="utf-8",
        )
        git(["add", "weld/graph.py"], cwd=self.root)
        git(["commit", "-m", "baseline"], cwd=self.root)

        # Two adversarial filenames in the new commit:
        #   - ``unicode_name``: pure non-ASCII chars (``é``) which git
        #     C-quotes to ``"h\303\251llo.txt"`` without
        #     ``-c core.quotePath=false``.
        #   - ``arrow_name``: literal ``" -> "`` substring + non-ASCII.
        #     Without ``-z`` (NUL separator) a record could collide with
        #     the rename arrow that some git output formats use.
        self.unicode_name = "héllo.txt"
        self.arrow_name = "weird -> héllo.txt"
        (self.root / self.unicode_name).write_text("u\n", encoding="utf-8")
        (self.root / self.arrow_name).write_text("a\n", encoding="utf-8")
        git(["add", "--", self.unicode_name, self.arrow_name], cwd=self.root)
        git(["commit", "-m", "add unicode files"], cwd=self.root)

    def test_diff_returns_utf8_unicode_filenames_verbatim(self) -> None:
        """Lock the unicode-path fix: ``-c core.quotePath=false`` + ``-z`` split.

        A regression that drops ``-c core.quotePath=false`` makes git
        emit ``"h\\303\\251llo.txt"`` (with surrounding quotes and
        octal-escaped bytes). A regression that switches back to
        ``splitlines`` would split the embedded ``" -> "`` filename
        incorrectly. Either failure mode shows up here.
        """
        from weld.impact_cli import _git_diff_files

        paths = _git_diff_files(self.root, "HEAD~1")

        # The exact UTF-8 strings must round-trip -- not C-quoted forms.
        self.assertIn(self.unicode_name, paths)
        self.assertIn(self.arrow_name, paths)

        # Negative assertions: catch the specific regression shapes.
        for entry in paths:
            self.assertFalse(
                entry.startswith('"') and entry.endswith('"'),
                f"diff path {entry!r} looks C-quoted (quotePath flag dropped?)",
            )
            self.assertNotIn(
                "\\303",
                entry,
                f"diff path {entry!r} contains octal escape "
                "(quotePath flag dropped?)",
            )

    def test_diff_handles_unicode_only_filename(self) -> None:
        """Single-file diff path: filename with only non-ASCII chars.

        Narrower than the combined assertion above so a regression
        produces a focused failure pointing directly at the
        non-ASCII-only case rather than at the ``" -> "`` interaction.
        """
        from weld.impact_cli import _git_diff_files

        paths = _git_diff_files(self.root, "HEAD~1..HEAD")

        self.assertIn(self.unicode_name, paths)
        self.assertNotIn(f'"{self.unicode_name}"', paths)


if __name__ == "__main__":
    unittest.main()
