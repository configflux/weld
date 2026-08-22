"""One home for ``weld/__init__.py``, held against the //weld package's own BUILD.

Bazel supplies a package's ``__init__.py`` itself (``legacy_create_init``) when
a target's runfiles carry ``weld/*.py`` but not the real file. weld had both
treatments at once -- 11 runfiles trees with a GENERATED empty file, 59 with a
symlink to the live source -- and the two are not interchangeable while trees
are being built: the generated form, written concurrently with trees holding
the symlink form, goes through the symlink and truncates the live source.
Serialised the interleaving never occurs; in parallel it does (bd p07k, with
the forensics and the refuted hypotheses in bd w3n6).

Nothing catches it downstream. ADR 0099 requires ``weld/__init__.py`` to be
empty, so truncating it is byte-identical: ``git status`` stays clean and the
porcelain-based gate diff cannot see it. What it costs is silent -- the bumped
ctime reads to Bazel as a concurrent modification of an input to hundreds of
py targets, and each one skips its disk-cache upload.

So the rule is structural, and this test is where it lives: **every py_ rule in
``weld/BUILD.bazel`` depends on ``:package_init``, and that target is the only
declaration of ``__init__.py`` as a Python source in the package.** A new leaf
library that forgets it reintroduces the second treatment, and the only symptom
would be cache loss.

**What this check cannot see, stated plainly.** The rule it holds is local to
one BUILD file, and the hazard is not: *any* py_ target in *any* package whose
runfiles end up containing ``weld/`` gets the synthesised file, whether it
depends on a weld library, stages weld sources as ``data``, or merely has its
own source under ``weld/`` so that ``weld/`` is an intermediate package
directory. Fixing the leaf libraries here took the count from 11 generated
trees to 3, and the survivors were exactly that third kind --
``:weld_rebrand_trace_test`` and ``:weld_determinism_hash_randomization_test``
(no deps at all, sources under ``weld/``) and
``//weld/tests/bench:synthetic_large_repo_test`` (deps that reach
``weld/bench/`` but never ``//weld``). Each now takes the dep explicitly.

Deciding that statically for an arbitrary package needs the resolved dep
graph, which a hermetic py_test does not have. So the complete check is
empirical and lives in bd p07k: ``find`` over a fully built ``bazel-out`` for
``*.runfiles/_main/weld/__init__.py`` must report symlinks only. That is what
measured 11 ``f`` / 59 ``l`` before and 0 ``f`` / 688 ``l`` after, and it is
what a change in this area has to re-run. A ``bazel query`` based gate check
that would catch it without a full build is filed separately.
"""

from __future__ import annotations

import os
import pathlib
import re
import unittest

_PY_RULE_RE = re.compile(r"^(py_library|py_binary|py_test)\(", re.MULTILINE)
_NAME_RE = re.compile(r"""^\s{4}name\s*=\s*["']([^"']+)["']""", re.MULTILINE)

#: The one target allowed to declare ``__init__.py`` in its ``srcs``, and the
#: one target that cannot depend on itself.
_INIT_HOME = "package_init"

#: Not a py_ rule: a filegroup that stages ``__init__.py`` + ``__main__.py`` as
#: *data* for sh_tests that launch ``-m weld`` from their runfiles. It is the
#: same source file, staged by a mechanism this rule does not govern, so the
#: source-declaration count below excludes it by name rather than by silence.
_FILEGROUP_STAGING = "module_entrypoint"


def _runfile(rel: str) -> pathlib.Path:
    """Resolve a repo-relative path inside the test's runfiles tree.

    ``TEST_SRCDIR``/``TEST_WORKSPACE`` are set by Bazel; falling back to the
    source tree keeps the file readable under a plain ``python -m unittest``.
    """
    srcdir = os.environ.get("TEST_SRCDIR")
    workspace = os.environ.get("TEST_WORKSPACE")
    if srcdir and workspace:
        return pathlib.Path(srcdir) / workspace / rel
    return pathlib.Path(__file__).resolve().parents[2] / rel


def _rule_blocks(text: str) -> list[tuple[str, str]]:
    """Return ``(rule_kind, block_text)`` for every top-level py_ rule.

    A block runs from its ``py_*(`` line to the closing ``\n)`` at column 0,
    which is what Buildifier's formatting guarantees for this file.
    """
    blocks: list[tuple[str, str]] = []
    for match in _PY_RULE_RE.finditer(text):
        end = text.find("\n)\n", match.start())
        end = len(text) if end == -1 else end + 3
        blocks.append((match.group(1), text[match.start() : end]))
    return blocks


def _target_name(block: str) -> str:
    named = _NAME_RE.search(block)
    return named.group(1) if named else ""


class PackageInitWiringTest(unittest.TestCase):
    """The //weld package's py_ rules all take __init__.py from one target."""

    def setUp(self) -> None:
        self.build_text = _runfile("weld/BUILD.bazel").read_text(encoding="utf-8")
        self.blocks = _rule_blocks(self.build_text)

    def test_build_file_declares_py_rules(self) -> None:
        """Guard the parse itself: a silent zero-block read would pass everything."""
        self.assertGreater(len(self.blocks), 5, self.build_text[:200])
        self.assertIn(_INIT_HOME, [_target_name(b) for _, b in self.blocks])

    def test_every_py_rule_depends_on_package_init(self) -> None:
        """The rule. A new leaf library that skips it fails here, not in a gate log."""
        missing = [
            name
            for _, block in self.blocks
            if (name := _target_name(block)) != _INIT_HOME
            and f'":{_INIT_HOME}"' not in block
        ]
        self.assertEqual(
            [],
            missing,
            "py_ targets in weld/BUILD.bazel with no dep on "
            f'":{_INIT_HOME}": {missing}. Bazel will synthesise its own empty '
            "weld/__init__.py into their runfiles trees, which truncates the "
            "live source when built concurrently with the symlink form "
            "(bd p07k).",
        )

    def test_package_init_is_the_only_source_declaration(self) -> None:
        """Two declarations build the same, and stop saying the rule out loud."""
        for kind, block in self.blocks:
            name = _target_name(block)
            if name == _INIT_HOME:
                self.assertIn('"__init__.py"', block, kind)
                continue
            self.assertNotIn(
                '"__init__.py"',
                block,
                f"{name} declares __init__.py in its own srcs; the one home is "
                f'"//weld:{_INIT_HOME}" (bd p07k).',
            )

    def test_runtime_srcs_does_not_redeclare_init(self) -> None:
        """``:runtime`` reaches __init__.py by dep, like every other target."""
        srcs_text = _runfile("weld/runtime_srcs.bzl").read_text(encoding="utf-8")
        self.assertNotIn('"__init__.py",', srcs_text)

    def test_filegroup_staging_is_still_the_data_path(self) -> None:
        """The one non-py_ stager is named here, so its absence would be noticed.

        ``:module_entrypoint`` hands ``__init__.py`` + ``__main__.py`` to
        sh_tests as data. If it ever disappeared, those tests would import
        ``weld`` as a namespace package with ``__file__`` set to ``None``, and
        this file is where a reader looks for what else touches the init.
        """
        self.assertIn(f'name = "{_FILEGROUP_STAGING}"', self.build_text)


if __name__ == "__main__":
    unittest.main()
