"""``wd add-node`` rejects an invalid ``props.enrichment`` at write time (ADR 0097).

Before ADR 0097 a record missing a required field (an agent omitting ``model``
is the observed case) was written happily and then *silently* stripped by the
next ``wd discover`` -- the user's work vanished with no error at write time and
no message at drop time. These tests pin the replacement contract: the write
fails, names the missing fields, and leaves the graph untouched.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from weld._errors import INVALID_ENRICHMENT
from weld._graph_cli import main

NODE = "symbol:py:pkg.mod:fn"

_COMPLETE = {
    "provider": "manual",
    "model": "agent-reviewed",
    "timestamp": "2026-08-13T00:00:00+00:00",
    "description": "What it does.",
}


class _Repo:
    """A temporary root with an empty graph, plus add-node helpers."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".weld").mkdir()

    def cleanup(self) -> None:
        self._tmp.cleanup()

    def add_node(self, props: dict, *, merge: bool = False) -> tuple[int, str]:
        """Run ``wd add-node``; return (exit code, stderr)."""
        argv = [
            "--root", str(self.root),
            "add-node", NODE, "--type", "symbol", "--label", "fn",
            "--props", json.dumps(props),
        ]
        if merge:
            argv.append("--merge")
        err = io.StringIO()
        try:
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                main(argv)
        except SystemExit as exc:
            return int(exc.code or 0), err.getvalue()
        return 0, err.getvalue()

    def node(self) -> dict | None:
        path = self.root / ".weld" / "graph.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))["nodes"].get(NODE)


class AddNodeEnrichmentValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = _Repo()
        self.addCleanup(self.repo.cleanup)

    def test_rejects_record_missing_a_field_and_names_it(self) -> None:
        incomplete = {k: v for k, v in _COMPLETE.items() if k != "model"}

        code, err = self.repo.add_node({"enrichment": incomplete})

        self.assertEqual(code, 1)
        self.assertIn(f"error[{INVALID_ENRICHMENT}]", err)
        self.assertIn("model", err)
        self.assertIn(NODE, err)

    def test_rejection_leaves_the_graph_unmutated(self) -> None:
        incomplete = {k: v for k, v in _COMPLETE.items() if k != "model"}

        self.repo.add_node({"enrichment": incomplete})

        self.assertIsNone(self.repo.node())

    def test_names_every_missing_field(self) -> None:
        code, err = self.repo.add_node({"enrichment": {"description": "d"}})

        self.assertEqual(code, 1)
        for field in ("provider", "model", "timestamp"):
            self.assertIn(field, err)

    def test_rejects_blank_field(self) -> None:
        code, err = self.repo.add_node({"enrichment": dict(_COMPLETE, model="  ")})

        self.assertEqual(code, 1)
        self.assertIn("model", err)

    def test_rejects_non_object_enrichment(self) -> None:
        code, err = self.repo.add_node({"enrichment": "reviewed by hand"})

        self.assertEqual(code, 1)
        self.assertIn(f"error[{INVALID_ENRICHMENT}]", err)

    def test_rejects_null_enrichment(self) -> None:
        # A JSON null is an invalid record, not an absent one -- it must not be
        # mistaken for "this write carries no enrichment".
        code, err = self.repo.add_node({"enrichment": None})

        self.assertEqual(code, 1)
        self.assertIn(f"error[{INVALID_ENRICHMENT}]", err)
        self.assertIsNone(self.repo.node())

    def test_rejection_never_echoes_record_values(self) -> None:
        # ADR 0035 no-leak: a rejected record can hold arbitrary text, so the
        # error names fields, never values.
        secret = "sk-live-not-a-real-token"
        code, err = self.repo.add_node(
            {"enrichment": dict(_COMPLETE, model="", description=secret)},
        )

        self.assertEqual(code, 1)
        self.assertNotIn(secret, err)

    def test_accepts_complete_record(self) -> None:
        code, err = self.repo.add_node({"enrichment": dict(_COMPLETE)})

        self.assertEqual(code, 0, err)
        self.assertEqual(self.repo.node()["props"]["enrichment"], _COMPLETE)

    def test_accepts_node_without_enrichment(self) -> None:
        code, err = self.repo.add_node({"file": "pkg/mod.py"})

        self.assertEqual(code, 0, err)
        self.assertIsNotNone(self.repo.node())

    def test_partial_merge_onto_complete_record_is_refused(self) -> None:
        # A record is one attestation and cannot be partially amended: merging
        # a bare description over an existing record would keep the previous
        # model/timestamp against the new text. The caller's own record is
        # judged, so a partial write is refused even though the merged result
        # would look complete.
        self.repo.add_node({"enrichment": dict(_COMPLETE)})

        code, err = self.repo.add_node(
            {"enrichment": {"description": "Sharper description."}}, merge=True,
        )

        self.assertEqual(code, 1)
        for field in ("provider", "model", "timestamp"):
            self.assertIn(field, err)
        self.assertEqual(self.repo.node()["props"]["enrichment"], _COMPLETE)

    def test_merge_of_a_whole_new_record_is_accepted(self) -> None:
        # Restating the record whole re-attests it: that is always allowed.
        self.repo.add_node({"enrichment": dict(_COMPLETE)})
        replacement = dict(_COMPLETE, provider="openai", model="gpt-x",
                           description="Sharper description.")

        code, err = self.repo.add_node({"enrichment": replacement}, merge=True)

        self.assertEqual(code, 0, err)
        self.assertEqual(self.repo.node()["props"]["enrichment"], replacement)

    def test_merge_that_blanks_a_required_field_is_rejected(self) -> None:
        self.repo.add_node({"enrichment": dict(_COMPLETE)})

        code, err = self.repo.add_node({"enrichment": {"model": ""}}, merge=True)

        self.assertEqual(code, 1)
        self.assertIn("model", err)
        # The pre-existing good record survives the rejected write.
        self.assertEqual(self.repo.node()["props"]["enrichment"], _COMPLETE)

    def test_unrelated_merge_preserves_a_valid_record(self) -> None:
        # A write that does not mention enrichment is judged on what it carries
        # forward, so an untouched valid record must not block the write.
        self.repo.add_node({"enrichment": dict(_COMPLETE)})

        code, err = self.repo.add_node({"file": "pkg/mod.py"}, merge=True)

        self.assertEqual(code, 0, err)
        self.assertEqual(self.repo.node()["props"]["enrichment"], _COMPLETE)

    def test_unrelated_merge_onto_a_legacy_invalid_record_is_refused(self) -> None:
        # A record written before this validation existed (or by a hand edit)
        # can still be sitting on disk. An unrelated --merge would carry it
        # forward untouched, so it is judged and refused -- the write is the
        # last moment anyone is looking at it.
        self.repo.add_node({"file": "pkg/mod.py"})
        path = self.repo.root / ".weld" / "graph.json"
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        on_disk["nodes"][NODE]["props"]["enrichment"] = {"provider": "manual"}
        path.write_text(json.dumps(on_disk), encoding="utf-8")

        code, err = self.repo.add_node({"file": "pkg/renamed.py"}, merge=True)

        self.assertEqual(code, 1)
        self.assertIn("model", err)
        self.assertEqual(self.repo.node()["props"]["file"], "pkg/mod.py")

    def test_merge_onto_missing_node_validates_the_new_record(self) -> None:
        # --merge with no existing node degrades to a plain write; the record
        # must still be judged, not waved through.
        code, err = self.repo.add_node(
            {"enrichment": {"description": "d"}}, merge=True,
        )

        self.assertEqual(code, 1)
        self.assertIn("provider", err)


if __name__ == "__main__":
    unittest.main()
