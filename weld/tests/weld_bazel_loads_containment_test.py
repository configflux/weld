"""Containment guards on ``load()``: what a hostile BUILD file may not do.

Split from ``weld_bazel_loads_test``, which sat at the 400-line cap so the
next assertion added to it -- the ``data``/``srcs`` ID-class pin bd i7ny
needed -- breached it. This is the cohesive half to move: the sibling file
asks what ``load()`` *resolves to*, and these two ask what it may *cost* and
*reach*, which is a security question with its own ADR (0025) and its own
failure mode.

Both cases exist because ``wd discover`` runs over repositories weld did not
write. A ``load()`` label is clean-looking input that reaches a filesystem
read, so the escape is the path it names (a symlink out of the tree) and the
denial of service is the fan-out it triggers (one BUILD file costing an
unbounded number of reads). Neither is visible in the resolver's own
docstring, which is why they are pinned here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies import bazel as bazel_module
from weld.strategies.bazel import extract


class LoadContainmentTest(unittest.TestCase):
    """A BUILD file is repository input that reaches a filesystem read.

    ``wd discover`` runs over repositories weld did not write (ADR 0025), so
    the two things a hostile ``load()`` could try -- read a file outside the
    tree, or make one BUILD file cost an unbounded number of reads -- are
    pinned here rather than left to the resolver's docstring.
    """

    def test_symlink_out_of_tree_is_not_read(self) -> None:
        """The label is clean; the *path it names* is the escape."""
        with tempfile.TemporaryDirectory() as outside_dir:
            secret = Path(outside_dir) / "secret.bzl"
            secret.write_text('STOLEN = ["a.py"]\n')
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "p").mkdir()
                (root / "p" / "escape.bzl").symlink_to(secret)
                (root / "p" / "BUILD.bazel").write_text(
                    'load(":escape.bzl", "STOLEN")\n'
                    'py_library(name = "lib", srcs = STOLEN)\n'
                )
                res = extract(root, {"glob": "**/BUILD.bazel"}, {})
        # The target still exists; only the escaping binding is missing, so
        # ``srcs`` resolves to nothing rather than to the file outside.
        self.assertIn("build-target://p:lib", res.nodes)
        contains = [e for e in res.edges if e["type"] == "contains"]
        self.assertEqual(contains, [])
        self.assertNotIn("p/escape.bzl", res.discovered_from)

    def test_fan_out_is_read_once_per_file(self) -> None:
        """Without memoisation this shape is F**D reads, not F*D."""
        fan, depth = 6, 5
        files: dict[str, str] = {}
        for level in range(depth):
            for i in range(fan):
                loads = "".join(
                    f'load(":m{level + 1}_{j}.bzl", "V{level + 1}_{j}")\n'
                    for j in range(fan)
                ) if level + 1 < depth else ""
                files[f"p/m{level}_{i}.bzl"] = loads + f'V{level}_{i} = ["x.py"]\n'
        files["p/BUILD.bazel"] = "".join(
            f'load(":m0_{i}.bzl", "V0_{i}")\n' for i in range(fan)
        ) + 'py_library(name = "lib", srcs = ["lib.py"])\n'

        reads: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, text in files.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            real_reader = bazel_module._bzl_reader

            def counting_reader(r):
                inner = real_reader(r)

                def read(rel_path: str):
                    reads.append(rel_path)
                    return inner(rel_path)

                return read

            bazel_module._bzl_reader = counting_reader
            try:
                extract(root, {"glob": "**/BUILD.bazel"}, {})
            finally:
                bazel_module._bzl_reader = real_reader

        self.assertLessEqual(len(reads), fan * depth + 1, len(reads))


if __name__ == "__main__":
    unittest.main()
