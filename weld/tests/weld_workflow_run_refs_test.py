"""Tests for :mod:`weld.strategies._workflow_run_refs` (bd lwrh).

A GitHub Actions ``run:`` step is shell text embedded in YAML. Neither
``gh_workflow`` nor ``yaml_meta`` parsed it, so a workflow that invoked a
repo script by path -- conditionally or otherwise -- had no edge to it: the
release-claim verifier's own gate (``tools/publish_overlays/publish-pypi.yml``
running ``tools/release_claims_lint.py``) was invisible to ``wd context``.

This module does not re-derive the shell grammar: it extracts the raw text
of every ``run:`` step and hands it to
:func:`weld.strategies._shell_refs.shell_text_references`, the same parser
``tool_script`` uses (ADR 0106), so a `run:` edge and a `tool_script` edge
are equally honest -- same comment rule, same safety refusals, same
"unresolved yields nothing" discipline.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._workflow_run_refs import workflow_script_references


def _touch(root: Path, rel: str, content: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class WorkflowScriptReferencesTest(unittest.TestCase):
    """What a workflow's ``run:`` steps name, and what they only appear to."""

    def test_inline_run_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/audit_publish.sh", "#!/bin/sh\n")
            text = (
                "jobs:\n"
                "  audit:\n"
                "    steps:\n"
                "      - name: Audit\n"
                "        run: tools/audit_publish.sh --dry-run\n"
            )
            self.assertEqual(
                ["tools/audit_publish.sh"], workflow_script_references(root, text)
            )

    def test_dash_prefixed_inline_run_resolves(self) -> None:
        # ``- run: <cmd>`` (no ``name:`` key) is common shorthand.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/lint.py", "")
            text = "jobs:\n  x:\n    steps:\n      - run: python3 tools/lint.py\n"
            self.assertEqual(["tools/lint.py"], workflow_script_references(root, text))

    def test_block_scalar_run_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/release_claims_lint.py", "")
            text = (
                "jobs:\n"
                "  pre-tag-verify:\n"
                "    steps:\n"
                "      - name: Run release-claim verifier\n"
                "        run: |\n"
                "          if [ -f tools/release_claims_lint.py ]; then\n"
                "            python tools/release_claims_lint.py --strict\n"
                "          fi\n"
            )
            self.assertEqual(
                ["tools/release_claims_lint.py"],
                workflow_script_references(root, text),
            )

    def test_conditional_invocation_still_counts(self) -> None:
        # The reference is the fact, regardless of the ``if`` guard around it
        # -- matching the dispatch's own acceptance framing.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/optional.py", "")
            text = (
                "jobs:\n"
                "  x:\n"
                "    steps:\n"
                "      - run: |\n"
                "          if [ -f tools/optional.py ]; then\n"
                "            python tools/optional.py\n"
                "          else\n"
                "            echo skip\n"
                "          fi\n"
            )
            self.assertEqual(["tools/optional.py"], workflow_script_references(root, text))

    def test_unresolvable_variable_path_yields_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/real.py", "")
            text = (
                "jobs:\n  x:\n    steps:\n"
                "      - run: |\n"
                "          python \"tools/${SCRIPT_NAME}.py\"\n"
            )
            self.assertEqual([], workflow_script_references(root, text))

    def test_run_block_stops_at_next_key(self) -> None:
        # A block scalar's body ends at the first line indented no more than
        # its own ``run:`` key -- content under a sibling ``env:`` (even one
        # that happens to look path-like) must not be swept in.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/inside.sh", "#!/bin/sh\n")
            _touch(root, "tools/outside.py", "")
            text = (
                "jobs:\n"
                "  x:\n"
                "    steps:\n"
                "      - name: Step\n"
                "        run: |\n"
                "          tools/inside.sh\n"
                "        env:\n"
                "          FOO: tools/outside.py\n"
            )
            self.assertEqual(["tools/inside.sh"], workflow_script_references(root, text))

    def test_non_run_yaml_content_is_not_scanned(self) -> None:
        # A path-like string elsewhere in the file (e.g. an ``env:`` default
        # with no run: step at all) is not shell text and must not produce
        # an edge -- the scope is run: steps, not the whole YAML document.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/mentioned.py", "")
            text = "env:\n  DEFAULT_SCRIPT: tools/mentioned.py\njobs: {}\n"
            self.assertEqual([], workflow_script_references(root, text))

    def test_comment_inside_run_block_is_not_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/real.sh", "#!/bin/sh\n")
            text = (
                "jobs:\n  x:\n    steps:\n"
                "      - run: |\n"
                "          # see tools/real.sh for details\n"
                "          echo hi\n"
            )
            self.assertEqual([], workflow_script_references(root, text))

    def test_multiple_run_steps_are_merged_sorted_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root, "tools/a.py", "")
            _touch(root, "tools/b.sh", "#!/bin/sh\n")
            text = (
                "jobs:\n"
                "  x:\n"
                "    steps:\n"
                "      - run: python3 tools/a.py\n"
                "      - run: bash tools/b.sh\n"
                "      - run: python3 tools/a.py\n"
            )
            self.assertEqual(
                ["tools/a.py", "tools/b.sh"], workflow_script_references(root, text)
            )

    def test_no_run_steps_yields_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = "name: CI\non: push\njobs:\n  x:\n    steps:\n      - uses: actions/checkout@v4\n"
            self.assertEqual([], workflow_script_references(root, text))

    def test_empty_text_yields_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual([], workflow_script_references(Path(tmp), ""))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
