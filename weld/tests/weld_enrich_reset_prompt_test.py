"""Tests for ``wd enrich --reset-prompt`` (ADR 0052).

The reset flag deletes ``.weld/.enrichment-prompted`` so the next
``wd discover`` can ask again. It exits 0 in both the "found and
cleared" and "no sentinel to clear" cases -- the operator should not
have to remember which one they're in.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._first_run_enrich import has_been_prompted, mark_prompted  # noqa: E402
from weld.enrich import main as enrich_main  # noqa: E402


class ResetPromptCliTest(unittest.TestCase):
    def test_reset_prompt_clears_existing_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mark_prompted(root, answer="no")
            self.assertTrue(has_been_prompted(root))

            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = enrich_main(["--root", str(root), "--reset-prompt"])
            self.assertEqual(rc, 0)
            self.assertFalse(has_been_prompted(root))
            self.assertIn("cleared", err.getvalue().lower())

    def test_reset_prompt_when_no_sentinel_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = enrich_main(["--root", str(root), "--reset-prompt"])
            self.assertEqual(rc, 0)
            self.assertFalse(has_been_prompted(root))
            self.assertIn("no sentinel", err.getvalue().lower())

    def test_reset_prompt_does_not_require_provider(self) -> None:
        # The reset path must short-circuit before any provider
        # resolution, so calling --reset-prompt without --provider or
        # WELD_ENRICH_PROVIDER is fine.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mark_prompted(root, answer="yes")
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                rc = enrich_main(["--root", str(root), "--reset-prompt"])
            self.assertEqual(rc, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
