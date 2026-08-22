"""Tests for the tool_script discovery strategy.

The strategy walks files matching ``glob`` and emits one
``tool:<rel-path-without-extension>`` node per file. The language label is
derived from the file suffix (``.py`` -> ``python``, ``.sh`` -> ``bash``)
and falls back to the shebang line for extensionless or atypically-named
scripts.

The ``invokes`` edges each script declares are a separate subject with its
own honesty rules, and live in ``weld_tool_script_invokes_test``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.tool_script import extract


def _touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestToolScriptEmptyAndMissing(unittest.TestCase):
    """Missing parent directory must yield a well-formed empty result."""

    def test_missing_parent_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertIsInstance(result, StrategyResult)
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])
            self.assertEqual(result.discovered_from, [])

    def test_directory_with_only_subdirs_returns_empty(self) -> None:
        # is_file() filter must keep directories out of the node map.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tools" / "nested").mkdir(parents=True)
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertEqual(result.nodes, {})


class TestToolScriptHappyPath(unittest.TestCase):
    """Suffix-based language detection covers the common case."""

    def test_python_suffix_yields_python_tool_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "audit.py", "print('hi')\n")
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertIn("tool:tools/audit", result.nodes)
            node = result.nodes["tool:tools/audit"]
            self.assertEqual(node["type"], "tool")
            self.assertEqual(node["label"], "audit.py")
            props = node["props"]
            self.assertEqual(props["file"], "tools/audit.py")
            self.assertEqual(props["lang"], "python")
            self.assertEqual(props["source_strategy"], "tool_script")
            self.assertEqual(props["authority"], "canonical")
            self.assertEqual(props["confidence"], "definite")
            self.assertEqual(props["roles"], ["script"])

    def test_shell_suffix_yields_bash_lang(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "release.sh", "#!/usr/bin/env bash\n")
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertIn("tool:tools/release", result.nodes)
            self.assertEqual(
                result.nodes["tool:tools/release"]["props"]["lang"], "bash"
            )


class TestToolScriptShebangFallback(unittest.TestCase):
    """Without a recognized suffix, the shebang line decides the language."""

    def test_shebang_python_on_extensionless_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(
                root / "tools" / "runner",
                "#!/usr/bin/env python3\nprint('hi')\n",
            )
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertIn("tool:tools/runner", result.nodes)
            self.assertEqual(
                result.nodes["tool:tools/runner"]["props"]["lang"], "python"
            )

    def test_unknown_suffix_without_recognizable_shebang_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # No suffix the strategy recognises and no language hint in
            # the first line: lang must be reported as 'unknown' rather
            # than guessed.
            _touch(root / "tools" / "mystery", "echo nothing helpful\n")
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertIn("tool:tools/mystery", result.nodes)
            self.assertEqual(
                result.nodes["tool:tools/mystery"]["props"]["lang"], "unknown"
            )

    def test_binary_without_shebang_yields_no_node(self) -> None:
        """Undecodable bytes still mean "not a script" (bd 2fe4).

        The bounded read replaced a whole-file ``read_text``; the
        pre-existing contract it must not drop is that a binary a broad
        glob happened to match yields no node rather than an
        ``unknown``-lang one.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "tools" / "blob"
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(b"\xff\xfe\x00\x01\x02binary payload\n")
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.discovered_from, [])

    def test_first_line_decides_without_reading_the_whole_file(self) -> None:
        """A huge extensionless file is classified from its head (bd 2fe4).

        The strategy used to read the entire file to look at one line --
        an unbounded read of a file weld did not write, for the exact
        class this fallback exists to serve. The observable contract is
        that the classification is correct and that everything past the
        first line is irrelevant to it: the body here is 4 MB of bytes
        that are *not* valid UTF-8, which a whole-file read would have
        raised on and skipped the node for.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            big = root / "tools" / "huge"
            big.parent.mkdir(parents=True, exist_ok=True)
            big.write_bytes(b"#!/usr/bin/env python3\n" + b"\xff" * (4 << 20))
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertIn("tool:tools/huge", result.nodes)
            self.assertEqual(
                result.nodes["tool:tools/huge"]["props"]["lang"], "python"
            )

    def test_long_non_ascii_first_line_is_not_mistaken_for_binary(self) -> None:
        """Truncation is not corruption.

        A bounded read can cut a multi-byte character in half. A strict
        ``bytes.decode`` cannot tell that from a genuine binary and raises
        for both, which would drop a legitimate script whose first line is
        long and non-ASCII. The head here is padded past the read bound
        with a two-byte character so the boundary lands mid-sequence.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "tools" / "accented"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(
                "#!/bin/bash  # " + ("é" * 400) + "\necho hi\n",
                encoding="utf-8",
            )
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertIn("tool:tools/accented", result.nodes)
            self.assertEqual(
                result.nodes["tool:tools/accented"]["props"]["lang"], "bash"
            )

    def test_dotted_stem_keeps_its_inner_dots(self) -> None:
        # Only the final extension is stripped. The stem-based minter
        # collapsed inner dots to underscores, which folded
        # ``build.helper.py`` and ``build_helper.py`` onto one id --
        # disambiguation traded away for a character-safety worry
        # ``weld._node_ids`` settled long ago (dot is a legal slug char).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "build.helper.py", "x = 1\n")
            _touch(root / "tools" / "build_helper.py", "x = 2\n")
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertIn("tool:tools/build.helper", result.nodes)
            self.assertIn("tool:tools/build_helper", result.nodes)


class TestToolScriptIdCollision(unittest.TestCase):
    """Path qualification, which is what let the scope widen past the root.

    ``tool:<stem>`` was safe only while one directory was in scope. Under
    ``tools/**/*.sh`` two same-named scripts in different directories would
    have merged into a single node carrying one of the two files' props --
    a node that is *real*, so no dangling-edge sweep and no lint can see it
    is wrong (bd x5ec, bd mdvp).
    """

    def test_same_stem_in_two_directories_stays_two_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "publish.sh", "#!/bin/sh\n")
            _touch(root / "tools" / "overlays" / "publish.sh", "#!/bin/sh\n")
            result = extract(root, {"glob": "tools/**/*.sh"}, {})
            self.assertEqual(
                sorted(result.nodes),
                ["tool:tools/overlays/publish", "tool:tools/publish"],
            )
            self.assertEqual(
                result.nodes["tool:tools/publish"]["props"]["file"],
                "tools/publish.sh",
            )
            self.assertEqual(
                result.nodes["tool:tools/overlays/publish"]["props"]["file"],
                "tools/overlays/publish.sh",
            )

    def test_extension_collision_is_named_rather_than_hidden(self) -> None:
        # The residual path qualification does not remove: the final
        # extension is stripped, so ``run.sh`` and ``run.py`` in one
        # directory are two files and one id. Keeping the extension would
        # fix it and cost a migration for every tool: node in existence, so
        # the collision is guarded instead -- the winner is deterministic
        # (sorted walk) and the loser is *named on the winner*. A node that
        # quietly stands for two files is the wrong-but-real node this whole
        # change refuses.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "run.sh", "#!/bin/sh\n")
            _touch(root / "tools" / "run.py", "x = 1\n")
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertEqual(["tool:tools/run"], list(result.nodes))
            props = result.nodes["tool:tools/run"]["props"]
            self.assertEqual("tools/run.py", props["file"])
            self.assertEqual(["tools/run.sh"], props["shadowed"])

    def test_a_shadowed_file_still_enters_provenance(self) -> None:
        # It was read, so an edit to it must re-run this strategy. Dropping
        # it from discovered_from would freeze the collision in place.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "run.sh", "#!/bin/sh\n")
            _touch(root / "tools" / "run.py", "x = 1\n")
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertEqual(
                ["tools/run.py", "tools/run.sh"], result.discovered_from
            )

    def test_no_shadowed_prop_when_there_is_no_collision(self) -> None:
        # The guard must be inert in the normal case; a prop that appears on
        # every node says nothing.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "run.sh", "#!/bin/sh\n")
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertNotIn("shadowed", result.nodes["tool:tools/run"]["props"])

    def test_root_script_id_is_unchanged_by_path_qualification(self) -> None:
        # The compatibility claim that made this landable without a graph
        # migration: at the root the qualified form and the stem form are
        # the same string, so every tool: node this repo already held keeps
        # its id.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "install.sh", "#!/bin/sh\n")
            _touch(root / "gradlew", "#!/usr/bin/env bash\n")
            result = extract(root, {"glob": "*"}, {})
            self.assertIn("tool:install", result.nodes)
            self.assertIn("tool:gradlew", result.nodes)


class TestToolScriptProvenance(unittest.TestCase):
    """``discovered_from`` names the matched files, never their directory.

    The directory form is unsafe for any root-anchored pattern:
    ``(root / "*.sh").parent`` is the root, which the strategy used to
    record as ``"./"`` -- and ``"./"`` is the marker
    :func:`weld._git._path_is_tracked` reads as "every path in this repo is
    tracked source". One root-level entry would have widened ``source_stale``
    to the whole tree.
    """

    def test_discovered_from_lists_matched_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "one.sh", "#!/usr/bin/env bash\n")
            _touch(root / "tools" / "two.sh", "#!/usr/bin/env bash\n")
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertEqual(
                result.discovered_from, ["tools/one.sh", "tools/two.sh"]
            )

    def test_root_anchored_pattern_does_not_emit_root_marker(self) -> None:
        # The regression. A root-level glob must not put "./" (or ".") into
        # provenance, whatever else it records.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "run-checks", "#!/usr/bin/env bash\n")
            result = extract(root, {"glob": "run-checks"}, {})
            self.assertEqual(result.discovered_from, ["run-checks"])
            self.assertNotIn("./", result.discovered_from)
            self.assertNotIn(".", result.discovered_from)

    def test_recursive_pattern_is_walked(self) -> None:
        # The old ``(root / pattern).parent.is_dir()`` guard returned empty
        # for any ``**`` pattern, because ``root/tools/**`` is not a
        # directory -- the strategy silently discovered nothing.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "nested" / "deep.sh", "#!/bin/sh\n")
            result = extract(root, {"glob": "tools/**/*.sh"}, {})
            self.assertIn("tool:tools/nested/deep", result.nodes)
            self.assertEqual(result.discovered_from, ["tools/nested/deep.sh"])

    def test_directories_are_not_recorded_as_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tools" / "nested").mkdir(parents=True)
            result = extract(root, {"glob": "tools/*"}, {})
            self.assertEqual(result.discovered_from, [])

    def test_missing_glob_key_returns_empty(self) -> None:
        # ``source["glob"]`` used to raise KeyError, which fails the whole
        # discovery run rather than declining this one entry.
        with tempfile.TemporaryDirectory() as tmp:
            result = extract(Path(tmp), {"type": "tool"}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.discovered_from, [])

    def test_excluded_file_is_absent_from_nodes_and_provenance(self) -> None:
        # ``should_skip`` now matches on the repo-relative path, the same
        # basis ``walk_glob`` prunes on, so a segmented exclude means the
        # same thing in both passes.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "tools" / "keep.sh", "#!/usr/bin/env bash\n")
            _touch(root / "tools" / "drop.sh", "#!/usr/bin/env bash\n")
            result = extract(
                root, {"glob": "tools/*", "exclude": ["tools/drop.sh"]}, {}
            )
            self.assertIn("tool:tools/keep", result.nodes)
            self.assertNotIn("tool:tools/drop", result.nodes)
            self.assertEqual(result.discovered_from, ["tools/keep.sh"])


if __name__ == "__main__":
    unittest.main()
