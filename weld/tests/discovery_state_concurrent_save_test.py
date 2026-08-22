"""Two concurrent ``save_state`` calls against the same root must not race.

bd 70he: ``weld_graph_integrity_regression_test`` and its BUILD-macro sibling
``weld_artifact_class_regression_test`` each independently call
``discover(live_repo_root, incremental=False)`` with no coordination between
them. Both land in ``_finalize_single_repo`` -> ``save_state_for_graph`` ->
``save_state`` for the SAME ``.weld/`` directory. When two Bazel test actions
schedule those targets concurrently, two ``save_state`` calls can be in
flight against the same root at once.

``save_state`` used to compute its temp file as a FIXED name
(``state_path.with_suffix(".tmp")``) before its atomic rename. Two
concurrent callers can interleave so the second rename targets a temp file
the first has already consumed (renamed away), raising
``FileNotFoundError`` -- an ``OSError`` that ``save_state``'s
``except OSError: ...; raise`` propagates straight out of ``discover()``.
That is an uncaught exception (unittest reports it as ERROR, not FAIL) from
a test that "passed in isolation immediately after" -- exactly what a
transient concurrency bug looks like, and exactly what a deterministic
stale-content read would not.

This test reproduces the interleaving deterministically -- no dependence on
real OS thread-scheduling luck -- by pausing the primary ``save_state`` call
right at its ``os.replace`` step (the shared final action both the old fixed-
name implementation and :func:`weld.workspace_state.atomic_write_text`'s
``tempfile.mkstemp``-based one funnel through: ``pathlib.Path.replace``
calls ``os.replace`` internally, so patching the latter intercepts either
implementation) until a sibling ``save_state`` call has fully completed. That
is precisely the "one writer's rename lands on a name the other just
consumed or is about to overwrite" window, without coupling the test to
which temp-naming scheme is currently in place.
"""

from __future__ import annotations

import os
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from weld.discovery_state import DiscoveryState, load_state, save_state


class ConcurrentSaveStateTest(unittest.TestCase):
    def test_two_concurrent_saves_against_the_same_root_do_not_raise(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir(parents=True, exist_ok=True)

            state_a = DiscoveryState(files={"a.py": "sha256:aaa"})
            state_b = DiscoveryState(files={"b.py": "sha256:bbb"})

            state_path = root / ".weld" / "discovery-state.json"
            primary_at_replace = threading.Event()
            sibling_done = threading.Event()
            original_replace = os.replace
            paused_once = threading.Event()

            def paced_replace(src: object, dst: object) -> None:
                # Only a rename landing THIS root's discovery-state.json is
                # of interest -- anything else (unittest/runtime internals on
                # another thread) must pass through untouched, or the pause
                # below could fire on an unrelated call and never form the
                # interleaving this test depends on.
                if Path(dst) != state_path:
                    original_replace(src, dst)
                    return
                # The primary call (running on this thread, synchronously
                # started below) is always the first to reach os.replace,
                # because the sibling thread blocks on primary_at_replace
                # before calling save_state at all. Only that first arrival
                # pauses; the sibling's own replace() (reached only after
                # the pause below releases it) passes straight through.
                if not paused_once.is_set():
                    paused_once.set()
                    primary_at_replace.set()
                    self.assertTrue(
                        sibling_done.wait(timeout=5),
                        "sibling save_state did not complete while the "
                        "primary call was paused at its own replace() -- "
                        "the interleaving this test depends on never formed",
                    )
                original_replace(src, dst)

            def run_sibling() -> None:
                self.assertTrue(
                    primary_at_replace.wait(timeout=5),
                    "primary save_state never reached replace()",
                )
                save_state(root, state_b)
                sibling_done.set()

            thread = threading.Thread(target=run_sibling)
            with mock.patch("os.replace", side_effect=paced_replace):
                thread.start()
                try:
                    save_state(root, state_a)  # must not raise
                except OSError as exc:  # pragma: no cover -- the bug this pins
                    self.fail(
                        f"save_state raised under concurrent siblings: "
                        f"{type(exc).__name__}: {exc}"
                    )
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "sibling thread did not finish")

            # Whichever writer's content ends up on disk (last-rename-wins
            # is an accepted outcome -- these two states describe different
            # runs, so there is no "correct" winner), it must be a complete,
            # loadable state: never a torn write, never a missing file.
            loaded = load_state(root)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertIn(loaded.files, (state_a.files, state_b.files))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
