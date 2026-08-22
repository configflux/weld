"""The ``wd find`` index admits extensionless scripts by their shebang.

``_is_indexed_file`` gated the index on two allow-lists -- a file extension
in ``INDEXED_EXTENSIONS`` or a basename in ``INDEXED_FILENAMES``. An
extensionless executable satisfies neither: ``Path("gradlew").suffix`` is
``""`` and the basename is whatever the project chose, so no allow-list
could ever carry it. The whole class of repo-root entry points was therefore
invisible to ``wd find`` -- ``gradlew``, ``configure``, ``mvnw``, ``manage``,
git hooks, and a project's own top-level task runner. Searching for one
answered "no matches" while its name sat in a file at the root of the
checkout, which reads as "absent" rather than "not indexed".

Admission was the only blocker: the tokenizer downstream already routes an
unrecognized extension to ``_extract_generic_tokens``, which is the right
reader for a shell script. So the fix is a third admission rule rather than
a new extractor, and these tests pin its edges.

The rule is deliberately narrow, because the alternative -- head-reading
every file whose extension is unrecognized -- would read binaries, archives,
and images on every index build:

* extensionless only, so a recognized-but-unindexed extension is untouched;
* non-hidden only, so ``.env`` and its secret-bearing siblings can never be
  drawn in by a stray first line (they share the empty-suffix property with
  every dotfile, which is what makes the exclusion load-bearing rather than
  cosmetic);
* ``#!`` at offset 0 only, read two bytes at a time, so a binary costs two
  bytes and a mention of ``#!`` further down the file proves nothing.

Closes bd 0edz.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.file_index import _is_indexed_file, build_file_index
from weld.file_index_search import find_files


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class ExtensionlessShebangAdmissionTest(unittest.TestCase):
    """The predicate itself: which extensionless files are text surface."""

    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_extensionless_shebang_script_is_indexed(self) -> None:
        # The regression: a top-level task runner, the shape of file that
        # exposed it.
        path = _write(
            self.root / "run-checks",
            "#!/usr/bin/env bash\nset -euo pipefail\nexec make check\n",
        )
        self.assertTrue(_is_indexed_file(path))

    def test_extensionless_python_shebang_is_indexed(self) -> None:
        # The interpreter named after ``#!`` is not inspected: any shebang
        # marks a script, and the tokenizer picks the reader by extension.
        path = _write(
            self.root / "manage", "#!/usr/bin/env python3\nimport sys\n"
        )
        self.assertTrue(_is_indexed_file(path))

    def test_extensionless_without_shebang_is_not_indexed(self) -> None:
        # LICENSE, AUTHORS, CODEOWNERS: extensionless prose is not a script
        # and must not widen the surface just by lacking a suffix.
        path = _write(self.root / "LICENSE", "Copyright (c) 2026\n")
        self.assertFalse(_is_indexed_file(path))

    def test_shebang_below_the_first_line_does_not_admit(self) -> None:
        # Only offset 0 is an interpreter directive. A ``#!`` anywhere else
        # is ordinary content, and accepting it would let any file argue
        # its way in by mentioning one.
        path = _write(self.root / "NOTES", "see below\n#!/usr/bin/env bash\n")
        self.assertFalse(_is_indexed_file(path))

    def test_leading_whitespace_before_shebang_does_not_admit(self) -> None:
        # The kernel does not accept it either, so neither does the index.
        path = _write(self.root / "spaced", " #!/usr/bin/env bash\n")
        self.assertFalse(_is_indexed_file(path))

    def test_binary_file_is_not_indexed(self) -> None:
        # Two bytes decide, so an ELF costs two bytes and is rejected on
        # magic rather than on a decode error.
        path = self.root / "a.out"
        path.write_bytes(b"\x7fELF\x02\x01\x01\x00" + bytes(64))
        # Guard the fixture: a suffixed name would pass for the wrong reason.
        self.assertEqual(path.suffix, ".out")
        self.assertFalse(_is_indexed_file(path))

    def test_extensionless_binary_is_not_indexed(self) -> None:
        path = self.root / "compiled"
        path.write_bytes(b"\x7fELF\x02\x01\x01\x00" + bytes(64))
        self.assertEqual(path.suffix, "")
        self.assertFalse(_is_indexed_file(path))

    def test_empty_extensionless_file_is_not_indexed(self) -> None:
        # A two-byte read returns fewer than two bytes; short reads must
        # compare unequal rather than raise.
        path = _write(self.root / "placeholder", "")
        self.assertFalse(_is_indexed_file(path))

    def test_one_byte_file_is_not_indexed(self) -> None:
        path = _write(self.root / "hash", "#")
        self.assertFalse(_is_indexed_file(path))

    def test_hidden_dotfile_with_shebang_is_not_indexed(self) -> None:
        # Load-bearing, not cosmetic: ``Path(".env").suffix`` is ``""``, so
        # every dotfile shares the extensionless property this rule keys on.
        # Secret-bearing dotfiles must not be drawn into a searchable index
        # by their first two bytes.
        path = _write(self.root / ".env", "#!/usr/bin/env bash\nTOKEN=shh\n")
        self.assertEqual(path.suffix, "")
        self.assertFalse(_is_indexed_file(path))

    def test_missing_file_is_not_indexed(self) -> None:
        # A file removed between the walk and the check must not raise.
        self.assertFalse(_is_indexed_file(self.root / "vanished"))

    def test_directory_is_not_indexed(self) -> None:
        # Extensionless directories are the common case; opening one raises
        # IsADirectoryError, which the predicate must absorb.
        path = self.root / "scripts"
        path.mkdir()
        self.assertFalse(_is_indexed_file(path))

    def test_allowlisted_extension_still_admitted_without_shebang(self) -> None:
        # The pre-existing rules keep priority; this is an added third rule,
        # not a replacement.
        path = _write(self.root / "mod.py", "x = 1\n")
        self.assertTrue(_is_indexed_file(path))

    def test_allowlisted_basename_still_admitted(self) -> None:
        path = _write(self.root / "BUILD.bazel", 'py_library(name = "x")\n')
        self.assertTrue(_is_indexed_file(path))

    def test_unrecognized_extension_stays_out(self) -> None:
        # The rule is extensionless-only. Widening it to "any unrecognized
        # extension with a shebang" would head-read every image and archive
        # in the tree on each index build.
        path = _write(self.root / "runner.bash", "#!/usr/bin/env bash\n")
        self.assertFalse(_is_indexed_file(path))


class ExtensionlessScriptIsFindableTest(unittest.TestCase):
    """End of the pipeline: admission must actually make ``wd find`` answer."""

    def test_built_index_makes_the_script_findable_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "run-checks",
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "printf 'checks in: %s\\n' \"${SCRIPT_DIR}\"\n"
                "exec make check\n",
            )
            index = build_file_index(root)
            self.assertIn("run-checks", index)

            # The originally-reported query, end to end over the real
            # search entry point rather than over the token list.
            hits = find_files(index, "run-checks")
            self.assertEqual(
                [f["path"] for f in hits["files"]], ["run-checks"]
            )

    def test_built_index_excludes_extensionless_prose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "LICENSE", "Copyright (c) 2026 example\n")
            self.assertNotIn("LICENSE", build_file_index(root))


class IncrementalSurfaceAgreesTest(unittest.TestCase):
    """The incremental refresh must see the same surface as the full walk.

    ``_surface_hashes`` gates on the same predicate, so a file admitted by
    the full walk but absent from the hash surface would be re-added and
    re-dropped on alternating refreshes. Asserting the agreement here keeps
    the two callers of ``_is_indexed_file`` from drifting.
    """

    def test_extensionless_script_is_in_both_surfaces(self) -> None:
        from weld._file_index_incremental import _surface_hashes

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "run-checks", "#!/usr/bin/env bash\nexec make check\n"
            )
            self.assertIn("run-checks", build_file_index(root))
            self.assertIn("run-checks", _surface_hashes(root))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
