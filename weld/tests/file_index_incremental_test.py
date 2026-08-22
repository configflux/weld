"""Incremental file-index refresh equals a full rebuild (bd 85tb.2).

The auto-refresh-on-read path re-tokenizes only the files whose content
changed since the last write instead of re-parsing every AST. The hard
bar is determinism: the incrementally-refreshed ``file-index.json`` must
be byte-identical to a full ``build_file_index`` + ``save_file_index`` at
the same on-disk state (ADR 0012 §3), and a changed/deleted file must
drop its stale tokens (no skipped invalidation).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


from weld._file_index_incremental import (  # noqa: E402
    STATE_FILENAME,
    refresh_file_index,
    reindex_full,
)
from weld.file_index import (  # noqa: E402
    build_file_index,
    load_file_index,
    save_file_index,
    tokens_for_file,
)


def _seed(root: Path) -> None:
    (root / "pkg").mkdir()
    (root / "pkg" / "alpha.py").write_text(
        "def alpha():\n    return 1\nALPHA_CONST = 1\n", encoding="utf-8",
    )
    (root / "pkg" / "beta.py").write_text(
        "def beta():\n    return 2\n", encoding="utf-8",
    )
    (root / "notes.md").write_text("# Title\n\nbody words here.\n", encoding="utf-8")
    (root / "config.yaml").write_text("top_key: 1\nother: 2\n", encoding="utf-8")
    (root / ".weld").mkdir()


def _full_index_bytes(root: Path) -> bytes:
    """Bytes a clean full rebuild would write."""
    save_file_index(root, build_file_index(root))
    raw = json.loads((root / ".weld" / "file-index.json").read_text(encoding="utf-8"))
    return json.dumps(raw, sort_keys=True).encode("utf-8")


class FileIndexIncrementalEquivalenceTest(unittest.TestCase):
    def test_tokens_for_file_matches_full_walk_entry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed(root)
            full = build_file_index(root)
            for rel in full:
                self.assertEqual(
                    tokens_for_file(root, rel), full[rel],
                    f"per-file tokenizer diverged from full walk for {rel}",
                )

    def test_modify_py_incremental_equals_full(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed(root)
            reindex_full(root)  # seeds companion
            (root / "pkg" / "alpha.py").write_text(
                "def alpha_renamed():\n    return 9\nNEW_CONST = 5\n",
                encoding="utf-8",
            )
            result = refresh_file_index(root)
            self.assertIsNotNone(result, "incremental refresh should engage")
            got = json.loads(
                (root / ".weld" / "file-index.json").read_text(encoding="utf-8"),
            )
            got_bytes = json.dumps(got, sort_keys=True).encode("utf-8")
            self.assertEqual(got_bytes, _full_index_bytes(root))
            # Stale body-derived token dropped, new ones present. ``alpha``
            # persists as the filename stem; the *function* token ``alpha``
            # is gone -- assert on the renamed identifier and dropped const.
            tokens = load_file_index(root)["pkg/alpha.py"]
            self.assertIn("alpha_renamed", tokens)
            self.assertIn("NEW_CONST", tokens)
            self.assertNotIn("ALPHA_CONST", tokens)

    def test_delete_file_incremental_equals_full(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed(root)
            reindex_full(root)
            (root / "pkg" / "beta.py").unlink()
            self.assertIsNotNone(refresh_file_index(root))
            got = json.loads(
                (root / ".weld" / "file-index.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                json.dumps(got, sort_keys=True).encode("utf-8"),
                _full_index_bytes(root),
            )
            self.assertNotIn("pkg/beta.py", load_file_index(root))

    def test_add_file_incremental_equals_full(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed(root)
            reindex_full(root)
            (root / "pkg" / "gamma.py").write_text(
                "def gamma():\n    return 3\n", encoding="utf-8",
            )
            self.assertIsNotNone(refresh_file_index(root))
            got = json.loads(
                (root / ".weld" / "file-index.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                json.dumps(got, sort_keys=True).encode("utf-8"),
                _full_index_bytes(root),
            )
            self.assertIn("pkg/gamma.py", load_file_index(root))

    def test_non_discover_doc_change_is_caught(self) -> None:
        """A changed doc that no discover source matches still re-tokenizes.

        The incremental refresh is driven by the file-index's own surface,
        not discovery-state's narrower source set, so a stale token must
        never survive a content change to a doc/markdown file.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed(root)
            reindex_full(root)
            # The markdown extractor harvests heading words, so change a
            # heading to a brand-new token (body prose is intentionally not
            # indexed). The stale heading word ``Title`` must drop.
            (root / "notes.md").write_text(
                "# Brandnewheading words\n\nbody text.\n", encoding="utf-8",
            )
            self.assertIsNotNone(refresh_file_index(root))
            tokens = load_file_index(root)["notes.md"]
            self.assertIn("Brandnewheading", tokens)
            self.assertNotIn("Title", tokens)
            got = json.loads(
                (root / ".weld" / "file-index.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                json.dumps(got, sort_keys=True).encode("utf-8"),
                _full_index_bytes(root),
            )

    def test_missing_companion_falls_back_to_full(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed(root)
            save_file_index(root, build_file_index(root))  # no companion written
            self.assertFalse((root / ".weld" / STATE_FILENAME).is_file())
            # No companion -> refresh declines (returns None) and the caller
            # rebuilds. We assert the decline so persist_file_index's fallback
            # is exercised on first run.
            self.assertIsNone(refresh_file_index(root))

    def test_wiped_index_with_companion_falls_back_to_full(self) -> None:
        """A present companion but missing file-index must rebuild, not blank."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed(root)
            reindex_full(root)
            (root / ".weld" / "file-index.json").unlink()
            # Companion still present, index gone -> must not carry over an
            # empty index; refresh declines so the full path reseeds.
            self.assertIsNone(refresh_file_index(root))

    def _read_companion(self, root: Path) -> dict:
        return json.loads(
            (root / ".weld" / STATE_FILENAME).read_text(encoding="utf-8"),
        )

    def _write_companion(self, root: Path, envelope: dict) -> None:
        (root / ".weld" / STATE_FILENAME).write_text(
            json.dumps(envelope, sort_keys=True), encoding="utf-8",
        )

    def test_stale_index_with_fresh_companion_is_rejected(self) -> None:
        """The flagged class: companion says "unchanged" but the index is stale.

        Reproduces the reported failure WITHOUT hand-forging hashes: the
        on-disk ``file-index.json`` is rolled back to an older state (a partial
        restore / single-file ``git checkout``) while the companion still
        describes the newer state. The companion's per-path hash for the edited
        file then equals the file's *current* content hash, so change-detection
        would mark it "unchanged" and carry the STALE tokens out of the rolled-
        back index -- diverging from a full rebuild. The integrity binding
        (companion records the sha256 of the index it describes) must catch the
        decoupling and decline incremental refresh so the caller rebuilds.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed(root)
            reindex_full(root)  # index@A + companion@A
            idx_path = root / ".weld" / "file-index.json"
            index_a_bytes = idx_path.read_bytes()  # OLD tokens for alpha
            # Edit alpha, then refresh so the on-disk pair advances to state B
            # (NEW tokens + companion recording alpha's NEW content hash).
            (root / "pkg" / "alpha.py").write_text(
                "def alpha_v2():\n    return 9\nNEW_CONST = 5\n", encoding="utf-8",
            )
            self.assertIsNotNone(refresh_file_index(root))  # index@B + companion@B
            # Roll the INDEX back to state A while leaving companion@B in place:
            # now the companion (NEW alpha hash) describes content that no longer
            # matches the rolled-back index (OLD alpha tokens).
            idx_path.write_bytes(index_a_bytes)
            # Without the binding, refresh would diff alpha's current content
            # hash against companion@B's matching NEW hash, call it "unchanged",
            # and serve index@A's stale ``alpha`` tokens. The binding rejects it.
            self.assertIsNone(
                refresh_file_index(root),
                "companion describing a different index than the on-disk one "
                "must force a full rebuild, not serve a stale carry-over",
            )

    def test_tampered_index_binding_forces_rebuild(self) -> None:
        """A companion whose recorded index hash is wrong is not trusted."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed(root)
            reindex_full(root)
            envelope = self._read_companion(root)
            # Corrupt the binding (any value that is not the on-disk index hash).
            envelope.setdefault("meta", {})["index_sha256"] = "0" * 64
            self._write_companion(root, envelope)
            self.assertIsNone(
                refresh_file_index(root),
                "a companion that does not bind to the on-disk index must "
                "decline incremental refresh",
            )

    def test_missing_index_binding_forces_rebuild(self) -> None:
        """A versioned companion with no index binding is not trusted."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed(root)
            reindex_full(root)
            envelope = self._read_companion(root)
            envelope.get("meta", {}).pop("index_sha256", None)
            self._write_companion(root, envelope)
            self.assertIsNone(
                refresh_file_index(root),
                "a companion lacking the index binding must decline so the "
                "caller rebuilds and reseeds a bound companion",
            )

    def test_chained_changes_never_drift_from_full(self) -> None:
        """No stale state accumulates across many incremental refreshes.

        Invalidation-safety (the security bar): after each step in a churn
        sequence -- modify, modify again, delete, re-add with new content --
        the incrementally-maintained index must still be byte-identical to a
        clean full rebuild at that exact on-disk state. A single skipped
        invalidation would surface here as drift on a later step.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed(root)
            reindex_full(root)
            alpha = root / "pkg" / "alpha.py"
            steps = [
                lambda: alpha.write_text("def v2():\n    return 2\n", encoding="utf-8"),
                lambda: alpha.write_text("def v3():\n    return 3\nC3 = 3\n", encoding="utf-8"),
                lambda: (root / "pkg" / "beta.py").unlink(),
                lambda: (root / "pkg" / "delta.py").write_text(
                    "def delta():\n    return 4\n", encoding="utf-8",
                ),
                lambda: (root / "notes.md").write_text("# Late\n\nx.\n", encoding="utf-8"),
            ]
            idx_path = root / ".weld" / "file-index.json"
            state_path = root / ".weld" / STATE_FILENAME
            for i, step in enumerate(steps):
                step()
                result = refresh_file_index(root)
                self.assertIsNotNone(result, f"step {i}: incremental should engage")
                # Snapshot the incrementally-maintained artifacts BEFORE the
                # full rebuild (which clobbers file-index.json), then restore
                # them so the next step continues from the incremental state.
                inc_index = idx_path.read_bytes()
                inc_state = state_path.read_bytes()
                got = json.loads(inc_index.decode("utf-8"))
                self.assertEqual(
                    json.dumps(got, sort_keys=True).encode("utf-8"),
                    _full_index_bytes(root),
                    f"step {i}: incremental index drifted from a full rebuild",
                )
                idx_path.write_bytes(inc_index)
                state_path.write_bytes(inc_state)


if __name__ == "__main__":
    unittest.main()
