"""The file index's own coverage signal (bd yw4b, ADR 0101's shape).

ADR 0101 gave the graph a coverage probe. The index could not borrow it: the
index surface is every repo-visible file the allow-list accepts, while the
graph's scope is ``discover.yaml``'s source globs, and the surface is the
strictly broader set. Every file in the gap is one the graph is *right* to
call itself fresh about -- so a ``.md`` note added to a warm checkout left
``stale: no, source_stale: no`` and ``wd find`` answering "no matches" about
it permanently.

The fixture's config globs ``*.py`` only, which makes ``notes.md`` exactly
such a file: inside the index surface, outside every source glob.

Filed as Mode B, but the misses reproduce in Mode A too, and the tests say so
-- a fresh Mode B clone has no basis at all, so the graph's own staleness
already schedules its rebuild; the warm checkout is where nothing did.
"""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from weld._file_index_coverage import (
    ensure_index_covers_surface,
    index_uncovered_files,
)
from weld.tests._mode_a_fixture import ModeAFixture
from weld.tests._seed_fixture import commit_all

_COMPANION = "file-index-state.json"


def _find(root, term: str, *flags: str) -> str:
    """Drive the real ``wd find`` against *root*; return stdout."""
    from weld._graph_cli import main as graph_main

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        graph_main(["--root", str(root), "find", term, *flags])
    return out.getvalue()


class IndexCoverageHealsTest(ModeAFixture):
    """A file inside the index surface but outside graph scope."""

    def _add_out_of_scope_file(self, name: str = "notes.md") -> None:
        (self.origin / name).write_text("searchable marker\n", encoding="utf-8")
        commit_all(self.origin, f"add {name} -- nothing in graph scope changed")

    def test_graph_is_right_to_call_itself_fresh(self) -> None:
        """The premise: no graph signal can see this file, and none should."""
        self._add_out_of_scope_file()

        from weld.tests._seed_fixture import stale_info

        info = stale_info(self.origin)

        self.assertFalse(info["source_stale"])
        self.assertFalse(info["coverage_stale"])

    def test_find_sees_the_file_the_graph_ignores(self) -> None:
        self._add_out_of_scope_file()

        self.assertIn("notes.md", _find(self.origin, "notes"))

    def test_uncovered_is_reported_before_the_repair(self) -> None:
        self._add_out_of_scope_file()

        self.assertIn("notes.md", index_uncovered_files(self.origin))

    def test_repair_is_recorded_and_converges(self) -> None:
        """One repair, then silence -- not a rebuild on every read."""
        self._add_out_of_scope_file()

        self.assertEqual(ensure_index_covers_surface(self.origin), 1)
        self.assertIsNone(ensure_index_covers_surface(self.origin))

    def test_uncommitted_file_is_covered_too(self) -> None:
        """The surface is the working tree, not the last commit."""
        (self.origin / "scratch.md").write_text("marker\n", encoding="utf-8")

        self.assertIn("scratch.md", _find(self.origin, "scratch"))


class IndexCoverageDoesNotLoopTest(ModeAFixture):
    """A healthy index must not be read as holed.

    The claim is taken from the companion, not the index body, precisely so
    a file that was read and drew no tokens -- omitted from the index by
    :func:`weld.file_index.build_file_index` -- is not mistaken for one that
    was never seen. Diffing the body instead would rebuild on every single
    ``find``, forever.
    """

    def test_warm_root_reports_nothing_uncovered(self) -> None:
        self.assertEqual(index_uncovered_files(self.origin), set())
        self.assertIsNone(ensure_index_covers_surface(self.origin))

    def test_claim_comes_from_the_companion_not_the_index_body(self) -> None:
        """A file the companion accounts for is covered, body entry or not.

        Constructed by dropping an entry from the index body and re-binding
        the companion to the index as rewritten, so the only disagreement
        left is the one under test. Reading coverage off the body instead
        would call this a hole and rebuild on every ``find`` forever -- the
        failure mode :func:`weld.file_index.build_file_index` invites by
        omitting any file it drew no tokens from.
        """
        from weld._file_index_incremental import (
            _load_state_hashes,
            _save_state_hashes,
        )
        from weld.file_index import load_file_index, save_file_index

        index = load_file_index(self.origin)
        claimed, _ = _load_state_hashes(self.origin)
        index.pop("alpha.py")
        save_file_index(self.origin, index)
        _save_state_hashes(self.origin, claimed)  # re-binds to the new body

        self.assertIn("alpha.py", claimed, "fixture: companion must claim it")
        self.assertEqual(index_uncovered_files(self.origin), set())
        self.assertIsNone(ensure_index_covers_surface(self.origin))


class IndexCoverageDeclinesTest(ModeAFixture):
    """Where nothing can vouch, the probe makes no claim."""

    def test_freeze_declines_and_writes_nothing(self) -> None:
        (self.origin / "notes.md").write_text("marker\n", encoding="utf-8")
        before = (self.origin / ".weld" / "file-index.json").read_bytes()

        with mock.patch.dict(os.environ, {"WELD_AUTO_REFRESH": "0"}):
            self.assertIsNone(ensure_index_covers_surface(self.origin))

        self.assertEqual(
            before, (self.origin / ".weld" / "file-index.json").read_bytes(),
        )

    def test_no_refresh_flag_declines(self) -> None:
        (self.origin / "notes.md").write_text("marker\n", encoding="utf-8")

        self.assertIsNone(
            ensure_index_covers_surface(self.origin, no_refresh=True),
        )

    def test_absent_companion_makes_no_claim(self) -> None:
        """No companion, no statement of what the index accounted for."""
        (self.origin / "notes.md").write_text("marker\n", encoding="utf-8")
        (self.origin / ".weld" / _COMPANION).unlink()

        self.assertEqual(index_uncovered_files(self.origin), set())

    def test_a_broken_probe_never_fails_the_search(self) -> None:
        """A self-heal that cannot run must not take the read down with it.

        The probe shells out to git for the boundary's file list, so what can
        go wrong here is not confined to ``OSError`` -- and a crashed
        ``wd find`` is strictly worse than the missed file the probe exists
        to prevent.
        """
        (self.origin / "notes.md").write_text("marker\n", encoding="utf-8")
        boom = mock.patch(
            "weld._file_index_coverage.index_uncovered_files",
            side_effect=RuntimeError("git went away"),
        )

        with boom:
            self.assertIsNone(ensure_index_covers_surface(self.origin))
            self.assertIn("alpha.py", _find(self.origin, "alpha"))

    def test_companion_bound_to_another_index_is_refused(self) -> None:
        """The binding half bd yw4b noted was already solved -- still is."""
        (self.origin / "notes.md").write_text("marker\n", encoding="utf-8")
        path = self.origin / ".weld" / _COMPANION
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["meta"]["index_sha256"] = "sha256:not-this-index"
        path.write_text(json.dumps(raw), encoding="utf-8")

        self.assertEqual(index_uncovered_files(self.origin), set())


if __name__ == "__main__":
    unittest.main()
