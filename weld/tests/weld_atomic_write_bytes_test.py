"""Unit tests for :func:`weld.workspace_state.atomic_write_bytes`.

The bytes variant mirrors :func:`atomic_write_text`: ``mkstemp`` in the
same directory plus :func:`os.replace`, with cleanup of the temp file on
any failure so callers see exactly the old bytes or exactly the new.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from weld.workspace_state import atomic_write_bytes


class AtomicWriteBytesTest(unittest.TestCase):
    def test_writes_bytes_to_final_path(self) -> None:
        with TemporaryDirectory() as tmp:
            final = Path(tmp) / ".weld" / "query_state.bin"
            atomic_write_bytes(final, b"\x80\x04payload")
            self.assertEqual(final.read_bytes(), b"\x80\x04payload")

    def test_creates_parent_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            final = Path(tmp) / "deep" / "path" / "blob.bin"
            atomic_write_bytes(final, b"x")
            self.assertTrue(final.is_file())

    def test_uses_os_replace_with_temp_in_same_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            final = Path(tmp) / "blob.bin"
            real_replace = os.replace
            with patch(
                "weld.workspace_state.os.replace",
                side_effect=real_replace,
            ) as replace_mock:
                atomic_write_bytes(final, b"content")
            replace_mock.assert_called_once()
            tmp_path, final_path = replace_mock.call_args.args
            self.assertEqual(Path(final_path), final)
            self.assertEqual(Path(tmp_path).parent, final.parent)
            self.assertIn("blob.bin.tmp.", Path(tmp_path).name)

    def test_temp_file_is_cleaned_on_error_during_write(self) -> None:
        """Failed rename must not leave partial final file or temp debris."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            final = root / "blob.bin"
            with patch(
                "weld.workspace_state.os.replace",
                side_effect=OSError("simulated mid-rename failure"),
            ), self.assertRaises(OSError):
                atomic_write_bytes(final, b"new")
            self.assertFalse(final.exists())
            self.assertFalse(
                any(".tmp." in p.name for p in root.iterdir()),
                f"temp-file debris: {[p.name for p in root.iterdir()]}",
            )

    def test_previous_content_preserved_on_error(self) -> None:
        """Atomic-replace semantics: existing final bytes survive a failure."""
        with TemporaryDirectory() as tmp:
            final = Path(tmp) / "blob.bin"
            final.write_bytes(b"prev")
            with patch(
                "weld.workspace_state.os.replace",
                side_effect=OSError("simulated"),
            ), self.assertRaises(OSError):
                atomic_write_bytes(final, b"new")
            self.assertEqual(final.read_bytes(), b"prev")


if __name__ == "__main__":
    unittest.main()
