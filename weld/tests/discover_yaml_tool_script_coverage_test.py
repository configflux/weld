"""Regression test: ``.weld/discover.yaml`` wires the tool_script strategy.

The ``tool_script`` strategy has always classified extensionless shebang
scripts -- ``weld_tool_script_strategy_test`` proves it by calling
``extract()`` directly. Nothing proved it was ever *called* here: this repo's
``.weld/discover.yaml`` carried no ``strategy: tool_script`` source entry, so
the graph held no ``tool:`` nodes at all and this repo's own root scripts --
including the extensionless task runner that its workflow docs invoke more
than any other command -- were absent from the connected structure.
``wd query`` ranked unrelated Python symbols for them and ``wd context`` had
nothing to resolve, so "what is the entry point, and who invokes it" had to
be answered with grep.

This is the same shipped-but-unwired shape as bd 180k (the bazel strategy),
and it is pinned the same way, because a strategy's own unit tests
structurally cannot catch it: they pass identically whether or not
``discover.yaml`` ever invokes the strategy. Closes bd 0edz.

The expectation is derived from the tree rather than from a hard-coded list,
so the next repo-root script fails this test -- and so forces a config edit --
instead of starting invisible and staying invisible until someone trips on
it. Scope is decided with :func:`weld._staleness_coverage.in_scope_files`,
the product's own "would discovery resolve this path?" matcher (ADR 0101),
so the config is asserted through the same code that decides coverage
staleness, with no discovery run and no git.

The repo's ``.weld/discover.yaml`` is internal state and is absent from the
published source tree, so the suite skips cleanly when it is not present.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from weld._staleness_coverage import in_scope_files
from weld._yaml import parse_yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DISCOVER_YAML = _REPO_ROOT / ".weld" / "discover.yaml"

_STRATEGY = "tool_script"

# The originating gap was filed against an *extensionless* root script, which
# is the shape no suffix glob can reach. Derived from the tree below rather
# than named here: hard-coding the basename would both restate what
# ``_root_scripts`` already computes and put a repo-internal tool name into a
# published file.

# ``tools/`` was the widening this file used to pin *out* of scope, deferred
# on ``tool:`` IDs being bare stems: under a directory tree ``tools/x.sh``
# and ``tools/sub/x.sh`` mint one node. ``weld._node_ids.tool_id``
# path-qualifies the ID, so the collision is gone by construction and the
# scope widened (bd x5ec). Pinned in the positive direction now: this is the
# script whose absence made every release tool look like an orphan with no
# runtime caller.
_IN_SCOPE_TOOLS_SCRIPT = "tools/bazel_cache_gc.sh"

# Still out of scope, and pinned so widening stays a conscious edit. Shell
# scripts under ``weld/tests`` are fixtures and harnesses for the suite, not
# operational entry points; claiming them as ``tool:`` nodes would bury this
# repo's actual tooling under its test scaffolding.
_OUT_OF_SCOPE_SCRIPT = "weld/tests/weld_discover_test.sh"

# Extensionless root files that are data, not scripts. They share the empty
# suffix that the shebang rule keys on, so they are the negative case that
# proves the derivation below reads content rather than guessing from names.
_NON_SCRIPT_ROOT_FILES = ("VERSION", "LICENSE", "NOTICE")


def _sources() -> list[dict]:
    """Parse the repo's checked-in ``.weld/discover.yaml`` source entries."""
    data = parse_yaml(_DISCOVER_YAML.read_text(encoding="utf-8"))
    sources = data.get("sources") or []
    return [entry for entry in sources if isinstance(entry, dict)]


def _tool_script_entries() -> list[dict]:
    return [e for e in _sources() if e.get("strategy") == _STRATEGY]


def _is_script(path: Path) -> bool:
    """Return True when *path* is a shell/interpreted script.

    A ``.sh`` suffix, or no suffix at all plus an interpreter directive in
    the first two bytes. Deliberately a local, literal rule rather than a
    call into ``weld.file_index``: deriving the expectation from the same
    code the config is checked through would make the assertion circular.
    """
    if path.suffix == ".sh":
        return True
    if path.suffix:
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(2) == b"#!"
    except OSError:
        return False


def _root_scripts() -> set[str]:
    """Every non-hidden script sitting directly at the repo root."""
    found: set[str] = set()
    for path in _REPO_ROOT.iterdir():
        if path.name.startswith(".") or path.is_symlink() or not path.is_file():
            continue
        if _is_script(path):
            found.add(path.name)
    return found


def _extensionless_root_scripts() -> set[str]:
    """Root scripts carrying no extension -- the class that exposed the gap."""
    return {name for name in _root_scripts() if not Path(name).suffix}


@unittest.skipUnless(
    _DISCOVER_YAML.is_file(),
    "repo .weld/discover.yaml not present (e.g. published source tree)",
)
class DiscoverYamlToolScriptCoverageTest(unittest.TestCase):
    """The tool_script strategy must be wired, and cover every root script."""

    def setUp(self) -> None:
        super().setUp()
        self.root_scripts = _root_scripts()
        # Guards the guard: an empty universe would make the coverage
        # assertion below vacuously true, which is the state this test exists
        # to detect.
        self.assertTrue(
            self.root_scripts,
            "found no scripts at the repo root; the derivation is broken or "
            "the checkout is incomplete",
        )

    def test_tool_script_strategy_is_configured(self) -> None:
        # The regression itself. The strategy passed its own tests for as
        # long as it has existed while never running here, because nothing
        # asserted that a shipped strategy is also a configured one.
        entries = _tool_script_entries()
        self.assertTrue(
            entries,
            "no 'strategy: tool_script' source entry in .weld/discover.yaml, "
            "so the graph holds no tool: nodes and the repo's own entry "
            "points are absent from the connected structure",
        )

    def test_every_root_script_is_in_scope(self) -> None:
        # The load-bearing assertion, and the reason the expectation comes
        # from the tree: a root script added tomorrow is covered the moment
        # it lands, or this fails and says so.
        covered = in_scope_files(_tool_script_entries(), sorted(self.root_scripts))
        missing = self.root_scripts - covered
        self.assertEqual(
            missing, set(),
            f"the tool_script entries do not cover {sorted(missing)}; every "
            "script at the repo root must be resolved by some entry",
        )

    def test_extensionless_root_scripts_are_covered(self) -> None:
        # The class the originating gap was filed against, and the one no
        # suffix pattern can reach.
        extensionless = _extensionless_root_scripts()
        self.assertTrue(
            extensionless,
            "no extensionless script at the repo root; the derivation "
            "drifted, and with it the regression this test guards",
        )
        missing = extensionless - in_scope_files(
            _tool_script_entries(), sorted(extensionless)
        )
        self.assertEqual(
            missing, set(),
            f"extensionless root scripts {sorted(missing)} are not covered -- "
            "the shape the originating dogfood gap was filed against",
        )

    def test_suffix_globs_alone_would_not_cover_them(self) -> None:
        # Pins the by-construction property that makes the config work at all.
        # An extensionless entry point cannot be matched by any ``*.<ext>``
        # pattern, so dropping the bare-name entry and keeping only a suffix
        # glob would restore the original failure while still satisfying
        # "a tool_script entry exists".
        suffix_only = [
            e for e in _tool_script_entries()
            if str(e.get("glob") or "").startswith("*.")
        ]
        self.assertEqual(
            in_scope_files(suffix_only, sorted(_extensionless_root_scripts())),
            set(),
            "a suffix glob matched an extensionless file; the bare-name "
            "entry is what carries this class and must stay",
        )

    def test_tools_directory_is_in_scope(self) -> None:
        # The widening. Every ``.sh`` under ``tools/`` must resolve, because
        # the shell scripts that run this repo's release tools live there and
        # a strategy cannot emit an ``invokes`` edge from a node that does
        # not exist.
        self.assertTrue(
            (_REPO_ROOT / _IN_SCOPE_TOOLS_SCRIPT).is_file(),
            "fixture path drifted; update _IN_SCOPE_TOOLS_SCRIPT",
        )
        tools_scripts = sorted(
            p.relative_to(_REPO_ROOT).as_posix()
            for p in (_REPO_ROOT / "tools").rglob("*.sh")
            if p.is_file() and not p.is_symlink()
        )
        self.assertIn(_IN_SCOPE_TOOLS_SCRIPT, tools_scripts)
        missing = set(tools_scripts) - in_scope_files(
            _tool_script_entries(), tools_scripts
        )
        self.assertEqual(
            missing, set(),
            f"tools/ scripts {sorted(missing)} are out of scope; the "
            "entries must cover the whole directory or the release "
            "pipeline stays half-modelled",
        )

    def test_scope_does_not_reach_the_test_harness(self) -> None:
        # The other direction, and the boundary that replaced "root only".
        # Shell scripts under weld/tests are suite fixtures; claiming them
        # would bury the repo's operational tooling under its scaffolding.
        self.assertTrue(
            (_REPO_ROOT / _OUT_OF_SCOPE_SCRIPT).is_file(),
            "fixture path drifted; update _OUT_OF_SCOPE_SCRIPT",
        )
        self.assertEqual(
            in_scope_files(_tool_script_entries(), [_OUT_OF_SCOPE_SCRIPT]),
            set(),
            f"{_OUT_OF_SCOPE_SCRIPT} is in scope; suite fixtures are not "
            "operational entry points and their coverage is a separate "
            "decision",
        )

    def test_non_script_root_files_are_not_claimed(self) -> None:
        # A bare ``*`` at the root would satisfy every assertion above while
        # minting tool: nodes for VERSION, LICENSE, and the READMEs.
        present = [
            name for name in _NON_SCRIPT_ROOT_FILES
            if (_REPO_ROOT / name).is_file()
        ]
        self.assertTrue(present, "fixture drifted; update _NON_SCRIPT_ROOT_FILES")
        self.assertEqual(
            in_scope_files(_tool_script_entries(), present), set(),
            f"{present} are data, not scripts, and must not be claimed as "
            "tool: nodes",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
