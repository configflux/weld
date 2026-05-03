"""Alias-population coverage for ADR 0041 PR 2/4 follow-up.

Two paths produce the same canonical node:

- agent-graph materialize: a SKILL.md asset and an agent ``uses_skills``
  reference both reach ``skill:generic:foo``. Pre-ADR-0041 the second
  arrival would have been minted as
  ``skill:generic:foo:<sha1(path)[:8]>``. The legacy form must now
  appear under ``props.aliases`` so external transcripts that reference
  the SHA1-suffixed ID still resolve via the alias-aware lookup.
- python_module: a top-level module under
  ``weld/strategies/python_module.py`` is canonicalised as
  ``file:weld/strategies/python_module``. The pre-rename
  ``file:python_module`` (bare-stem) form must appear under
  ``props.aliases`` for the same reason.

ros2 strategies already populate ``aliases`` for the
``ros_package:<name>`` -> ``package:ros2:<name>`` rename; that path is
covered by ``weld_ros2_package_test.py`` and not retested here.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.agent_graph_discovery import discover_agent_graph  # noqa: E402
from weld.agent_graph_materialize import (  # noqa: E402
    legacy_skill_id_with_suffix,
)
from weld.strategies.python_module import (  # noqa: E402
    _legacy_stem_file_id,
    extract as python_module_extract,
)


def _write(root: Path, rel_path: str, text: str) -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class LegacySkillIdHelperTest(unittest.TestCase):
    """The legacy SHA1-suffix helper reproduces the pre-ADR-0041 form."""

    def test_helper_format_is_canonical_plus_sha1_suffix(self) -> None:
        # Same shape as the historical ``_node_id_for_values`` collision
        # path: ``<base>:<sha1(path)[:8]>``.
        legacy = legacy_skill_id_with_suffix(
            "skill", "generic", "architecture-decision",
            "skills/architecture-decision/SKILL.md",
        )
        self.assertTrue(legacy.startswith("skill:generic:architecture-decision:"))
        suffix = legacy.rsplit(":", 1)[-1]
        # 8 hex chars -- matches hashlib.sha1(...).hexdigest()[:8].
        self.assertEqual(len(suffix), 8)
        self.assertTrue(all(c in "0123456789abcdef" for c in suffix))

    def test_helper_is_deterministic(self) -> None:
        a = legacy_skill_id_with_suffix("skill", "generic", "foo", "a.md")
        b = legacy_skill_id_with_suffix("skill", "generic", "foo", "a.md")
        self.assertEqual(a, b)


class AgentGraphMaterializeAliasPopulationTest(unittest.TestCase):
    """Two SKILL.md assets for the same skill leave their legacy SHA1
    forms on the merged canonical node's ``aliases`` list."""

    def test_aliases_contain_each_path_specific_legacy_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                "skills/architecture-decision/SKILL.md",
                "---\nname: architecture-decision\n---\nbody\n",
            )
            _write(
                root,
                "examples/demo/skills/architecture-decision/SKILL.md",
                "---\nname: architecture-decision\n---\nbody\n",
            )

            graph = discover_agent_graph(
                root,
                git_sha="fixture",
                updated_at="2026-05-02T00:00:00+00:00",
            )

        node = graph["nodes"]["skill:generic:architecture-decision"]
        aliases = node["props"]["aliases"]
        # Each asset path produces one SHA1-suffix legacy ID; merged
        # node should contain both.
        expected_a = legacy_skill_id_with_suffix(
            "skill", "generic", "architecture-decision",
            "skills/architecture-decision/SKILL.md",
        )
        expected_b = legacy_skill_id_with_suffix(
            "skill", "generic", "architecture-decision",
            "examples/demo/skills/architecture-decision/SKILL.md",
        )
        self.assertIn(expected_a, aliases)
        self.assertIn(expected_b, aliases)


class PythonModuleAliasPopulationTest(unittest.TestCase):
    """``python_module`` records the bare-stem legacy ID under aliases."""

    def test_canonical_node_aliases_include_legacy_stem_form(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                "weld/strategies/sample_module.py",
                textwrap.dedent(
                    """\
                    def public():
                        return 1


                    class PublicClass:
                        pass
                    """
                ),
            )
            result = python_module_extract(
                root, {"glob": "weld/strategies/*.py"}, {},
            )
        canonical_id = "file:weld/strategies/sample_module"
        legacy_id = "file:sample_module"
        self.assertIn(canonical_id, result.nodes)
        self.assertEqual(_legacy_stem_file_id("weld/strategies/sample_module.py"), legacy_id)
        self.assertEqual(
            result.nodes[canonical_id]["props"]["aliases"], [legacy_id],
        )

    def test_alias_omitted_when_legacy_equals_canonical(self) -> None:
        # A single-segment module at the repo root would canonicalise
        # to ``file:thing`` and the legacy ``file:thing`` is identical;
        # in that case ``aliases`` must remain empty rather than
        # carrying a duplicate self-alias.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                "thing.py",
                "def public(): return 1\n",
            )
            result = python_module_extract(root, {"glob": "*.py"}, {})
        canonical_id = "file:thing"
        self.assertIn(canonical_id, result.nodes)
        self.assertEqual(result.nodes[canonical_id]["props"]["aliases"], [])


if __name__ == "__main__":
    unittest.main()
