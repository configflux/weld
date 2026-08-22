"""Which ``sys.path`` entry the ``python -m weld`` entry point removes.

``python -m`` prepends the process working directory to ``sys.path`` ahead of
the standard library. For the CLI that directory is the repository being
scanned, so ``python -m weld discover --safe`` against an untrusted checkout
would otherwise execute that checkout's ``json.py``, ``subprocess.py`` or
``socket.py`` on the way up -- before safe mode gets a say, since safe mode
governs strategy loading and network egress, not the import path.

These are the in-process tests. They pin that the shared mechanism accepts
the CLI entry's own ``__main__`` name, and that it stays inert for every
process that merely imported weld. That the removal happens *early enough* to
beat the CLI's own imports cannot be observed from inside the process;
``weld_cli_launch_shadow_test`` proves that part by launching the real thing.

The mechanism itself -- which entries count, which are deliberately left
alone -- is pinned once in ``weld_mcp_launch_path_test`` and not repeated
here.
"""

from __future__ import annotations

import os
import sys
import types
import unittest

from weld import _launch_path


class CliEntryGuardTest(unittest.TestCase):
    """The CLI entry point is guarded under its own ``__main__`` name."""

    #: What ``python -m weld`` records as ``__main__.__spec__.name``. Running
    #: a *package* runs its ``__main__`` submodule, so the name weld has to
    #: match on is the submodule's, not the package's -- the one difference
    #: between this entry point and ``python -m weld.mcp_server``.
    ENTRY = "weld.__main__"

    def test_guard_drops_the_launch_directory_for_the_cli_entry(self) -> None:
        # Staged rather than launched: in process, the most that can be shown
        # is that the mechanism fires for this name. The launch form is
        # `weld_cli_launch_shadow_test`'s subject.
        main = sys.modules["__main__"]
        original_spec = getattr(main, "__spec__", None)
        original_path = list(sys.path)
        cwd = os.getcwd()
        sys.path[:] = [cwd, "/site-packages"]
        main.__spec__ = types.SimpleNamespace(name=self.ENTRY)
        try:
            removed = _launch_path.guard_module_launch(self.ENTRY)
            remaining = list(sys.path)
        finally:
            main.__spec__ = original_spec
            sys.path[:] = original_path

        self.assertEqual(removed, cwd)
        self.assertEqual(remaining, ["/site-packages"])

    def test_guard_is_inert_when_the_cli_entry_is_not_main(self) -> None:
        # This test process is the shape that matters: it imported weld
        # rather than being launched as it, which is every library, test
        # runner and embedding host.
        before = list(sys.path)

        removed = _launch_path.guard_module_launch(self.ENTRY)

        self.assertIsNone(removed)
        self.assertEqual(sys.path, before)

    def test_importing_the_cli_entry_module_leaves_sys_path_alone(self) -> None:
        # The guard runs as a side effect of executing weld/__main__.py, and
        # that module is importable like any other. Importing it must not
        # rearrange the importer's path.
        before = list(sys.path)

        import weld.__main__  # noqa: F401

        self.assertEqual(sys.path, before)


if __name__ == "__main__":
    unittest.main()
