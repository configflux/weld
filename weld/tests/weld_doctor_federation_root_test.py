"""``wd doctor`` at a root that federates and discovers nothing of its own.

A federated ``wd discover`` reads ``.weld/workspaces.yaml`` and the children's
graphs and resolves no source glob at the root, so a root that only federates
has nothing of its own to discover and needs no ``discover.yaml``. Doctor
graded that absence a ``[fail]``, so the healthiest possible workspace root
reported ``Status: errors`` and exited 1 while ``wd workspace status`` reported
every child green (field-eval v0.25.0 finding M3, bd 5038-lcq0c.5). The cost is
not the line of output: agents gate on doctor's exit code, so a correct setup
reads as a broken one.

ADR 0141 D3 is the decision these cases pin: at a root holding
``workspaces.yaml``, an absent ``discover.yaml`` is a **note**; a root that
also discovers, and a plain repository, keep the behaviour they had.

Both halves are asserted, and the second is the load-bearing one -- a
carve-out that swallows the check everywhere is a worse bug than the one it
fixes, since a genuinely uninitialised project would then report healthy.

These cases ask :func:`weld.workspace_state.find_workspaces_yaml`'s question
rather than one of their own, which is why the registry is placed at both
spellings that function accepts. Doctor disagreeing with the read path about
what a federation root is *is* the finding; a second detector spelled locally
in :mod:`weld._doctor_config` would reproduce it one file over.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weld.doctor import doctor, main as doctor_main

_WORKSPACES_YAML = """\
version: 1
children:
  - name: services-api
    path: services/api
"""

_DISCOVER_YAML = """\
sources:
  - glob: "src/**/*.py"
    type: file
    strategy: python_module
"""


def _minimal_graph() -> str:
    return json.dumps({"meta": {"schema_version": 4}, "nodes": {}, "edges": []})


def _weld_project(root: Path, *, workspaces: str | None, discover: bool) -> Path:
    """Materialise a ``.weld/`` project; return the ``.weld`` directory.

    *workspaces* is the path the registry is written to relative to *root*
    (``None`` for a plain repository), so one builder covers both spellings
    ``find_workspaces_yaml`` accepts.
    """
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True)
    (weld_dir / "graph.json").write_text(_minimal_graph(), encoding="utf-8")
    if workspaces is not None:
        (root / workspaces).write_text(_WORKSPACES_YAML, encoding="utf-8")
    if discover:
        (weld_dir / "discover.yaml").write_text(_DISCOVER_YAML, encoding="utf-8")
    return weld_dir


def _config_findings(results) -> list:
    """Every result the ``discover.yaml`` check produced."""
    return [r for r in results if "discover.yaml" in r.message and r.section == "Config"]


class DoctorAtAPureFederationRoot(unittest.TestCase):
    """``workspaces.yaml`` present, ``discover.yaml`` absent -- the M3 shape."""

    def test_the_absent_config_is_a_note_not_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces=".weld/workspaces.yaml", discover=False)

            findings = _config_findings(doctor(root))

            self.assertEqual(
                len(findings), 1,
                f"expected exactly one finding about discover.yaml, got {findings}",
            )
            self.assertEqual(findings[0].level, "note", findings[0].message)
            # The note has to say *why* the file is absent, or it is a failure
            # with a quieter level -- the reader still has to guess whether to
            # run `wd init`. Asserted on the word the message is about rather
            # than the whole sentence, which is free to be reworded.
            self.assertIn("federat", findings[0].message.lower(), findings[0].message)

    def test_no_check_fails_at_a_healthy_federation_root(self) -> None:
        """The verdict, not just this one row: nothing else picks up the slack.

        Asserting only that this check stopped failing would pass if some
        neighbouring check started failing on the same absent file, which is
        exactly what a reader gating on the exit code would still meet.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces=".weld/workspaces.yaml", discover=False)

            failures = [r for r in doctor(root) if r.level == "fail"]

            self.assertEqual(failures, [], f"doctor fails a healthy root: {failures}")

    def test_doctor_exits_zero(self) -> None:
        """The consequence the finding is about: agents gate on this number."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces=".weld/workspaces.yaml", discover=False)
            output = io.StringIO()

            with patch("sys.stdout", output):
                code = doctor_main(["--root", str(root)])

            printed = output.getvalue()
            self.assertEqual(code, 0, printed)
            self.assertNotIn("[fail]", printed)
            self.assertNotIn("Status: errors", printed)

    def test_the_registry_is_found_where_the_read_path_looks_for_it(self) -> None:
        """A root-level ``workspaces.yaml`` federates too, so doctor agrees.

        ``find_workspaces_yaml`` accepts ``.weld/workspaces.yaml`` *and* a
        top-level ``workspaces.yaml``; every graph-backed read decides
        federation by asking it. Doctor calling only one of the two a
        federation root would put the same disagreement back, one spelling
        over.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces="workspaces.yaml", discover=False)

            findings = _config_findings(doctor(root))

            self.assertEqual(len(findings), 1, f"{findings}")
            self.assertEqual(findings[0].level, "note", findings[0].message)


class DoctorWhereTheCarveOutMustNotReach(unittest.TestCase):
    """The two shapes ADR 0141 D3 leaves exactly as they were."""

    def test_a_plain_repository_still_fails(self) -> None:
        """No registry, no config -- an uninitialised project, and still an error.

        The regression guard for the fix above: a carve-out that swallowed
        this case would report every un-``wd init``-ed checkout healthy, which
        is a worse failure than the one M3 reports.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces=None, discover=False)

            findings = _config_findings(doctor(root))

            self.assertEqual(len(findings), 1, f"{findings}")
            self.assertEqual(findings[0].level, "fail", findings[0].message)

    def test_a_plain_repository_still_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces=None, discover=False)
            output = io.StringIO()

            with patch("sys.stdout", output):
                code = doctor_main(["--root", str(root)])

            self.assertEqual(code, 1, output.getvalue())

    def test_a_root_that_federates_and_discovers_is_unchanged(self) -> None:
        """Both files present: the ordinary ``[ok]`` with its source count.

        A workspace root may hold sources of its own (shared tooling, a
        top-level service); federation says nothing about that, and the note
        would be a lie there.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _weld_project(root, workspaces=".weld/workspaces.yaml", discover=True)

            findings = _config_findings(doctor(root))

            self.assertEqual(len(findings), 1, f"{findings}")
            self.assertEqual(findings[0].level, "ok", findings[0].message)
            self.assertIn("1 source entry", findings[0].message)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
