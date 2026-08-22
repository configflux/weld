"""Which ``sys.path`` entry the MCP stdio entry point removes, and which not.

``python -m`` prepends the process working directory to ``sys.path`` ahead of
the standard library, and MCP clients launch servers with the project
directory as that working directory. Anything the server imports could
otherwise be answered -- and executed -- from the repository being served.

These are the in-process tests: they pin *which* entry is removed and, just
as importantly, which ones are left alone -- a ``PYTHONPATH`` duplicate, an
interpreter started with ``-P``, a path the guard cannot make sense of, and
any process that merely imported the module rather than being launched as it.
Removing one entry too many would be its own outage.

That the removal happens *early enough* to beat the entry module's own
imports cannot be observed from inside the process; ``weld_mcp_launch_shadow_test``
proves that part by launching the real thing.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest

from weld import _launch_path


class LaunchEntryIdentificationTest(unittest.TestCase):
    """Which sys.path entry counts as the launch directory."""

    def test_absolute_working_directory_is_the_launch_entry(self) -> None:
        cwd = os.getcwd()

        self.assertTrue(_launch_path.is_launch_directory(cwd, cwd))

    def test_relative_spellings_are_the_launch_entry(self) -> None:
        # Python 3.11 made the entry an absolute path; before that it was the
        # empty string, resolved against the working directory at each
        # import. weld supports both interpreters, so both must be caught.
        for spelling in ("", os.curdir):
            with self.subTest(spelling=spelling):
                self.assertTrue(
                    _launch_path.is_launch_directory(spelling, os.getcwd())
                )

    def test_unrelated_directory_is_not_the_launch_entry(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            self.assertFalse(_launch_path.is_launch_directory(other, os.getcwd()))

    def test_unstattable_entry_falls_back_to_path_comparison(self) -> None:
        # sys.path routinely carries entries that do not exist on disk (zip
        # imports, stale PYTHONPATH). samefile raises on those, and raising
        # here would take the server down instead of hardening it.
        missing = os.path.join(os.getcwd(), "does-not-exist-9d3f")

        self.assertFalse(_launch_path.is_launch_directory(missing, os.getcwd()))


class DropLaunchDirectoryTest(unittest.TestCase):
    """What is removed, and -- more delicately -- what is not."""

    def test_removes_the_leading_launch_entry(self) -> None:
        cwd = os.getcwd()
        path = [cwd, "/usr/lib/python3", "/site-packages"]

        removed = _launch_path.drop_launch_directory(path, cwd)

        self.assertEqual(removed, cwd)
        self.assertEqual(path, ["/usr/lib/python3", "/site-packages"])

    def test_keeps_a_pythonpath_duplicate_of_the_same_directory(self) -> None:
        # Only index 0 is the entry `python -m` inserted. A PYTHONPATH that
        # also names the directory is the user's own declaration about their
        # own environment; editing it would be a second surprise on top of
        # the one being fixed.
        cwd = os.getcwd()
        path = ["", cwd, "/site-packages"]

        removed = _launch_path.drop_launch_directory(path, cwd)

        self.assertEqual(removed, "")
        self.assertEqual(path, [cwd, "/site-packages"])

    def test_leaves_a_path_that_never_had_the_entry(self) -> None:
        # `-P` / PYTHONSAFEPATH=1 start the interpreter without it, so index
        # 0 is a legitimate entry that must survive untouched.
        path = ["/site-packages", "/usr/lib/python3"]
        before = list(path)

        removed = _launch_path.drop_launch_directory(path, os.getcwd())

        self.assertIsNone(removed)
        self.assertEqual(path, before)

    def test_is_idempotent(self) -> None:
        cwd = os.getcwd()
        path = [cwd, "/site-packages"]

        _launch_path.drop_launch_directory(path, cwd)
        second = _launch_path.drop_launch_directory(path, cwd)

        self.assertIsNone(second)
        self.assertEqual(path, ["/site-packages"])

    def test_empty_path_is_not_an_error(self) -> None:
        self.assertIsNone(_launch_path.drop_launch_directory([], os.getcwd()))


class GuardInertnessTest(unittest.TestCase):
    """Importing weld must never rearrange somebody else's sys.path."""

    def test_guard_does_not_fire_for_a_foreign_main(self) -> None:
        # This test process is the case that matters: it imported the module
        # rather than being launched as it. Nothing may have been removed.
        before = list(sys.path)

        removed = _launch_path.guard_module_launch()

        self.assertIsNone(removed)
        self.assertEqual(sys.path, before)

    def test_guard_does_not_fire_for_a_different_module(self) -> None:
        before = list(sys.path)

        removed = _launch_path.guard_module_launch("weld.definitely_not_main")

        self.assertIsNone(removed)
        self.assertEqual(sys.path, before)

    def test_guard_does_not_fire_when_main_has_no_spec(self) -> None:
        # `python script.py` and embedded interpreters leave __main__ with no
        # spec at all, which has to read as "not us" rather than raise. The
        # test process always has one, so the state is staged explicitly --
        # otherwise this test would only ever re-run the case above.
        main = sys.modules["__main__"]
        original_spec = getattr(main, "__spec__", None)
        before = list(sys.path)
        main.__spec__ = None
        try:
            removed = _launch_path.guard_module_launch()
        finally:
            main.__spec__ = original_spec

        self.assertIsNone(removed)
        self.assertEqual(sys.path, before)

    def test_guard_survives_an_unusable_sys_path(self) -> None:
        # sys.path is a plain list anyone may have put anything in. Failing
        # to harden it is a bad outcome; refusing to start the server over it
        # is a worse one, so the guard must swallow whatever it hits.
        main = sys.modules["__main__"]
        original_spec = getattr(main, "__spec__", None)
        original_path = list(sys.path)
        sys.path[:] = [object()]  # type: ignore[list-item]
        main.__spec__ = types.SimpleNamespace(name="weld.mcp_server")
        try:
            removed = _launch_path.guard_module_launch()
        finally:
            main.__spec__ = original_spec
            sys.path[:] = original_path

        self.assertIsNone(removed)

    def test_importing_the_mcp_server_leaves_sys_path_alone(self) -> None:
        # The guard rides in on an import of weld.mcp_server, so the library
        # import path is the one that would break every embedding host.
        before = list(sys.path)

        from weld import mcp_server  # noqa: F401

        self.assertEqual(sys.path, before)


if __name__ == "__main__":
    unittest.main()
