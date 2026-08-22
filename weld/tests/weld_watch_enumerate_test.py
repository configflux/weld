"""Tests for ``weld.watch._default_enumerate`` -- the watch file census.

``wd watch`` decides *what to poll* by resolving every source entry in
``.weld/discover.yaml`` through
:func:`weld._source_resolve.resolve_source_files`. That call sits behind the
CLI's backend wiring (``watch.main`` hands ``_default_enumerate`` to
``get_backend``), so no other test reaches it -- a signature drift on the
resolver would surface only at runtime. These tests cover it directly.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld import watch


CONFIG = """sources:
  - glob: "src/**/*.py"
    strategy: python_module
    type: file
    exclude:
      - "src/vendor/**"
  - path: "README.md"
    strategy: markdown
    type: file
"""


class DefaultEnumerateTests(unittest.TestCase):
    def test_enumerates_glob_and_path_sources_honouring_excludes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="watch-enum-") as td:
            root = Path(td)
            (root / ".weld").mkdir()
            (root / ".weld" / "discover.yaml").write_text(CONFIG, encoding="utf-8")
            (root / "README.md").write_text("# demo\n", encoding="utf-8")
            vendor = root / "src" / "vendor"
            vendor.mkdir(parents=True)
            (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
            (vendor / "dep.py").write_text("y = 2\n", encoding="utf-8")

            files = watch._default_enumerate(root)

        self.assertIn("README.md", files)
        self.assertIn(str(Path("src/app.py")), files)
        self.assertNotIn(str(Path("src/vendor/dep.py")), files)
        self.assertEqual(files, sorted(files))

    def test_returns_empty_without_a_config(self) -> None:
        with tempfile.TemporaryDirectory(prefix="watch-noconf-") as td:
            self.assertEqual(watch._default_enumerate(Path(td)), [])


if __name__ == "__main__":
    unittest.main()
