"""Tests for the bounded shell-script reference extractor (bd x5ec).

``weld.strategies._shell_refs`` answers one question: which repo-relative
scripts does this shell script name in its own body? It is the evidence
behind every ``invokes`` edge ``tool_script`` emits, so what it refuses
matters as much as what it finds -- an over-eager reading turns ``invokes``
into ``mentions`` and inflates every blast radius that joins through it,
while an under-eager one leaves the release pipeline's control flow invisible
all over again.

The safety cases are not decoration. Discovery runs against arbitrary user
repositories, so a path literal must never be able to steer a read outside
the worktree.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from weld.strategies._shell_refs import (
    script_references,
    shell_text_references,
    strip_comment,
)


def _touch(root: Path, rel: str, content: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class StripCommentTest(unittest.TestCase):
    """The POSIX rule, not "everything after the first #"."""

    def test_word_initial_hash_opens_a_comment(self) -> None:
        self.assertEqual("echo hi ", strip_comment("echo hi # tools/x.sh"))

    def test_leading_hash_comments_the_whole_line(self) -> None:
        self.assertEqual("", strip_comment("# see tools/x.sh"))

    def test_hash_inside_a_word_is_not_a_comment(self) -> None:
        # ``${VAR#prefix}`` and ``file#anchor`` are ordinary words. Cutting
        # at them would silently truncate a real path.
        self.assertEqual("run tools/a#b.sh", strip_comment("run tools/a#b.sh"))

    def test_quoted_hash_is_not_a_comment(self) -> None:
        self.assertEqual('echo "a # b"', strip_comment('echo "a # b"'))

    def test_hash_after_a_separator_opens_a_comment(self) -> None:
        self.assertEqual("run; ", strip_comment("run; # tools/x.sh"))


class ScriptReferencesTest(unittest.TestCase):
    """What a script names, and what it only appears to name."""

    def test_plain_repo_relative_path_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/helper.py", "x = 1\n")
            _touch(root, "tools/run.sh", "#!/bin/sh\npython3 tools/helper.py\n")
            self.assertEqual(
                ["tools/helper.py"], script_references(root, "tools/run.sh")
            )

    def test_variable_prefix_resolves_against_the_referrer_directory(self) -> None:
        # ``${SCRIPT_DIR}/x.sh`` is how a script locates its own siblings.
        # The prefix is stripped and the remainder must name a real file --
        # the existence check, not the variable's value, is the safety.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/sub/helper.sh", "#!/bin/sh\n")
            _touch(
                root, "tools/sub/run.sh",
                '#!/bin/sh\nbash "${SCRIPT_DIR}/helper.sh"\n',
            )
            self.assertEqual(
                ["tools/sub/helper.sh"],
                script_references(root, "tools/sub/run.sh"),
            )

    def test_nested_variable_prefixes_are_stripped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/publish.sh", "#!/bin/sh\n")
            _touch(
                root, "tools/run.sh",
                '#!/bin/sh\n"${REPO_ROOT}/tools/publish.sh"\n',
            )
            self.assertEqual(
                ["tools/publish.sh"], script_references(root, "tools/run.sh")
            )

    def test_dot_slash_prefix_is_stripped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/helper.sh", "#!/bin/sh\n")
            _touch(root, "tools/run.sh", "#!/bin/sh\n./tools/helper.sh\n")
            self.assertEqual(
                ["tools/helper.sh"], script_references(root, "tools/run.sh")
            )

    def test_unresolved_variable_in_the_middle_yields_nothing(self) -> None:
        # ``tools/${NAME}.sh`` is not a statically known path. Guessing at
        # one is how a referrer lands an edge on a real but wrong node.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/helper.sh", "#!/bin/sh\n")
            _touch(root, "tools/run.sh", '#!/bin/sh\n"tools/${NAME}.sh"\n')
            self.assertEqual([], script_references(root, "tools/run.sh"))

    def test_nonexistent_path_yields_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/run.sh", "#!/bin/sh\npython3 src/auth.py\n")
            self.assertEqual([], script_references(root, "tools/run.sh"))

    def test_self_reference_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(
                root, "tools/run.sh",
                '#!/bin/sh\n: "${V:?run.sh: V not set}"\n',
            )
            self.assertEqual([], script_references(root, "tools/run.sh"))

    def test_result_is_sorted_and_deduplicated(self) -> None:
        # ADR 0012 §3: output order is a property of the tree, not of the
        # order the referring file happens to mention things in.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/a.sh", "#!/bin/sh\n")
            _touch(root, "tools/b.sh", "#!/bin/sh\n")
            _touch(
                root, "tools/run.sh",
                "#!/bin/sh\ntools/b.sh\ntools/a.sh\ntools/b.sh\n",
            )
            self.assertEqual(
                ["tools/a.sh", "tools/b.sh"],
                script_references(root, "tools/run.sh"),
            )

    def test_missing_file_returns_empty_rather_than_raising(self) -> None:
        # Discovery runs mid-orchestration over every matched path; a file
        # that vanished between the walk and the read must not take the run
        # down (the pt38 failure shape).
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual([], script_references(Path(tmp), "tools/gone.sh"))


class ScriptReferencesSafetyTest(unittest.TestCase):
    """A path literal must never steer a read outside the worktree."""

    def test_absolute_path_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/run.sh", "#!/bin/sh\npython3 /etc/passwd.py\n")
            self.assertEqual([], script_references(root, "tools/run.sh"))

    def test_parent_traversal_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            _touch(root.parent, "outside.py", "x = 1\n")
            _touch(root, "tools/run.sh", "#!/bin/sh\npython3 ../outside.py\n")
            self.assertEqual([], script_references(root, "tools/run.sh"))

    def test_home_relative_path_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/run.sh", "#!/bin/sh\npython3 ~/secrets.py\n")
            self.assertEqual([], script_references(root, "tools/run.sh"))

    def test_symlinked_referent_is_refused(self) -> None:
        # A symlink inside the repo can point anywhere. The node would be
        # minted for the link path while the bytes came from elsewhere.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            outside = Path(tmp) / "outside.py"
            outside.write_text("x = 1\n", encoding="utf-8")
            (root / "tools").mkdir()
            try:
                os.symlink(outside, root / "tools" / "linked.py")
            except (OSError, NotImplementedError):  # pragma: no cover
                self.skipTest("symlinks unavailable on this platform")
            _touch(root, "tools/run.sh", "#!/bin/sh\npython3 tools/linked.py\n")
            self.assertEqual([], script_references(root, "tools/run.sh"))

    def test_non_script_extension_is_not_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/data.yaml", "a: 1\n")
            _touch(root, "tools/run.sh", "#!/bin/sh\ncat tools/data.yaml\n")
            self.assertEqual([], script_references(root, "tools/run.sh"))

    def test_undecodable_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tools").mkdir()
            (root / "tools" / "run.sh").write_bytes(b"\xff\xfe\x00binary")
            self.assertEqual([], script_references(root, "tools/run.sh"))

    def test_read_is_byte_bounded(self) -> None:
        # The line cap alone still reads the whole file into memory first,
        # and a path matching ``*.sh`` in a user repo may be an artefact of
        # any size. Anything past the char cap is simply not seen.
        from weld.strategies._shell_refs import _MAX_CHARS

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/late.sh", "#!/bin/sh\n")
            padding = "#!/bin/sh\n" + ("x" * _MAX_CHARS) + "\n"
            _touch(root, "tools/run.sh", padding + "tools/late.sh\n")
            self.assertEqual([], script_references(root, "tools/run.sh"))

    def test_referent_count_is_bounded(self) -> None:
        # A script that enumerates the tree records no relationship worth
        # having; the cap is what keeps one from producing hundreds of
        # generic edges (the lint_repo.py lesson in validator_targets).
        from weld.strategies._shell_refs import _MAX_REFS

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = ["#!/bin/sh"]
            for index in range(_MAX_REFS + 20):
                _touch(root, f"tools/gen{index}.sh", "#!/bin/sh\n")
                body.append(f"tools/gen{index}.sh")
            _touch(root, "tools/run.sh", "\n".join(body) + "\n")
            self.assertEqual(
                _MAX_REFS, len(script_references(root, "tools/run.sh"))
            )


class ShellTextReferencesTest(unittest.TestCase):
    """:func:`shell_text_references` -- the same grammar, for shell text that
    is not itself a file on disk (a GitHub Actions ``run:`` block, bd lwrh).
    """

    def test_plain_repo_relative_path_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/helper.py", "x = 1\n")
            self.assertEqual(
                ["tools/helper.py"],
                shell_text_references(root, "python3 tools/helper.py\n"),
            )

    def test_comment_is_stripped(self) -> None:
        # Same POSIX rule as script_references -- a mentioned-in-a-comment
        # path is not evidence of an invocation.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/helper.py", "x = 1\n")
            self.assertEqual(
                [], shell_text_references(root, "# see tools/helper.py\n")
            )

    def test_unresolved_variable_yields_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/helper.sh", "#!/bin/sh\n")
            self.assertEqual(
                [], shell_text_references(root, '"tools/${NAME}.sh"\n')
            )

    def test_nonexistent_path_yields_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                [], shell_text_references(root, "python3 src/auth.py\n")
            )

    def test_absolute_path_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                [], shell_text_references(root, "python3 /etc/passwd.py\n")
            )

    def test_result_is_sorted_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/a.sh", "#!/bin/sh\n")
            _touch(root, "tools/b.sh", "#!/bin/sh\n")
            self.assertEqual(
                ["tools/a.sh", "tools/b.sh"],
                shell_text_references(root, "tools/b.sh\ntools/a.sh\ntools/b.sh\n"),
            )

    def test_sibling_dir_resolves_variable_prefixed_paths(self) -> None:
        # A caller anchored somewhere other than the repo root (this
        # module's own sibling_dir parameter) still resolves
        # ``${SCRIPT_DIR}``-style references against that directory.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/sub/helper.sh", "#!/bin/sh\n")
            self.assertEqual(
                ["tools/sub/helper.sh"],
                shell_text_references(
                    root, 'bash "${SCRIPT_DIR}/helper.sh"\n', sibling_dir="tools/sub"
                ),
            )

    def test_empty_text_yields_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual([], shell_text_references(Path(tmp), ""))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
