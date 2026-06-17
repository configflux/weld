"""Static validation of ``.github/workflows/graph-artifact.yml`` (ADR 0067).

GitHub Actions billing is unavailable in this environment, so the
graph-artifact workflow cannot be exercised against live CI. This test is the
standing static guard: it parses the workflow with weld's bundled YAML parser
(``yamllint`` / ``actionlint`` are not available here) and asserts the
load-bearing contract -- it triggers on pushes to ``main``, builds the graph
with ``wd discover --safe``, publishes a per-commit integrity tag, and uploads
the graph artifact keyed by the commit SHA. A regression that drops the safe
flag, the hash step, or the SHA-keyed upload fails here at merge time.

The bundled parser now expands multi-line literal/folded block scalars
(bd kooo), so the ``run:`` hash command and the ``path:`` upload list are
asserted against the parsed mapping like every other field -- no raw-text
fallback.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from weld._yaml import parse_yaml  # noqa: E402

_WORKFLOW = _repo_root / ".github" / "workflows" / "graph-artifact.yml"


def _load() -> dict:
    data = parse_yaml(_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "workflow must parse to a mapping"
    return data


class GraphArtifactWorkflowTest(unittest.TestCase):
    def test_workflow_file_exists(self) -> None:
        self.assertTrue(_WORKFLOW.is_file(), f"missing {_WORKFLOW}")

    def test_parses_and_names_itself(self) -> None:
        self.assertEqual(_load().get("name"), "Graph Artifact")

    def test_triggers_on_push_to_main(self) -> None:
        # YAML 1.1 would coerce a bare ``on`` to True; the bundled parser keeps
        # it the string key 'on', which is what GitHub Actions expects.
        wf = _load()
        self.assertIn("on", wf)
        self.assertEqual(wf["on"].get("push", {}).get("branches"), ["main"])

    def test_permissions_are_read_only(self) -> None:
        self.assertEqual(_load().get("permissions", {}).get("contents"), "read")

    def test_build_step_uses_safe_discover(self) -> None:
        steps = _load()["jobs"]["build-graph"]["steps"]
        build = self._step(steps, "Build graph")
        self.assertIn("wd discover", build["run"])
        self.assertIn("--safe", build["run"])
        self.assertIn(".weld/graph.json", build["run"])

    def test_integrity_tag_command_in_block_scalar(self) -> None:
        # The hash command lives in a ``run: |`` block scalar; the parser now
        # expands it, so assert the command from the parsed step body.
        steps = _load()["jobs"]["build-graph"]["steps"]
        integrity = self._step(steps, "Compute integrity tag")
        self.assertIn("sha256sum .weld/graph.json", integrity["run"])
        self.assertIn("graph.json.sha256", integrity["run"])

    def test_upload_step_is_keyed_by_commit_sha(self) -> None:
        steps = _load()["jobs"]["build-graph"]["steps"]
        upload = self._step(steps, "Upload graph artifact")
        self.assertIn("upload-artifact", upload["uses"])
        # ``name`` is a single-line value the parser handles.
        self.assertIn("github.sha", str(upload["with"]["name"]))

    def test_upload_paths_from_parsed_block_scalar(self) -> None:
        # ``path:`` is a multi-line literal block scalar the parser expands into
        # a newline-joined string; assert both files and the sibling keys that
        # follow the block survive the expansion.
        steps = _load()["jobs"]["build-graph"]["steps"]
        upload = self._step(steps, "Upload graph artifact")
        path = upload["with"]["path"]
        self.assertIn(".weld/graph.json", path)
        self.assertIn(".weld/graph.json.sha256", path)
        self.assertEqual(upload["with"]["if-no-files-found"], "error")

    @staticmethod
    def _step(steps: list, name: str) -> dict:
        for step in steps:
            if step.get("name") == name:
                return step
        raise AssertionError(f"workflow step not found: {name!r}")


if __name__ == "__main__":
    unittest.main()
