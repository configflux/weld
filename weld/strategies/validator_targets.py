"""Strategy: ``validates`` edges from a lint/checker to the paths it polices.

A repository's validators are structure. ``tools/lint_launch_entrypoints.py``
is the reason ``weld/__init__.py`` must stay import-free; ``markdown_lint.py``
is the reason a given doc is shaped the way it is. None of that reached the
connected structure, because the relationship is carried by *neither* of the
edges discovery already recovers: a lint reads its subject at runtime through
``ast.parse``/``open``, so there is no import edge and no call edge joining
them. An agent asking "what constrains this file" before editing it got
nothing back, and edited it anyway (bd tz51).

This strategy recovers the missing hop from the one place the relationship is
actually written down: the path literals inside the validator module. Every
string constant in the module is scanned for repo-relative paths and glob
patterns; each one that resolves to a real file in the worktree becomes

    ``file:<validator>`` --``validates``--> ``<target id>``

``validates`` is the existing ADR 0016 governance verb (validator-subject
assertions); no new edge type is introduced.

Boundary and safety rules:

- Literal harvesting is bounded: over-long strings are skipped, the path
  pattern itself caps match length, and the literal count per module is
  capped. Discovery runs against arbitrary user repositories.
- Paths are sanitized before they reach the filesystem or the graph:
  absolute paths, ``..`` traversal, symlinks, and anything that resolves
  outside the repository root are dropped. A literal must name a file that
  exists in the worktree to become an edge.
- Glob literals expand through
  :func:`weld.strategies._glob_resolve.resolve_glob` (which prunes ignored
  directories and never follows symlinks) and are dropped entirely past
  :data:`_MAX_GLOB_EXPANSION`. A validator that governs the
  whole tree by file extension -- ``lint_repo.py`` and the line-count cap --
  has no per-file relationship worth recording; expanding it would bury the
  specific edges under a thousand generic ones.
- Confidence is ``inferred`` throughout. A path literal is evidence of
  governance, not proof of it, and the ``inferred`` rank is what makes the
  minted stub below safe: ADR 0103's ``claim_supersedes`` veto guarantees an
  ``inferred`` claim can never overwrite a ``definite`` node another strategy
  already recorded for the same ID.

Target IDs come from the shared :mod:`weld.strategies._target_ids` rule: emit
each plausible spelling and let the post-processor's dangling-edge sweep keep
the ones that resolve. ``concept_from_bd`` -- the other strategy that refers to
paths it does not mint -- goes through the same rule, because the copy it used
to keep predated the ADR 0041 ``file:`` rename and silently emitted nothing but
dangling edges for months.

One node is minted rather than merely referenced. A ``.py`` target that exists
on disk but yields no ``python_module`` anchor -- the export-less
``__init__.py`` class -- gets a minimal ``file:`` stub, because that is exactly
the blind spot this strategy exists to close: ``weld/__init__.py`` is governed
precisely *because* it is empty, and an empty module is the one thing ADR 0041
declines to anchor. Targets that would anchor under some other configuration
(``examples/**`` and friends) are never minted -- discovery scope stays a
config decision.

The stub is also why this strategy is the one that found the incremental
merge's dirty-scope conflation (bd n0p2): a *newly* governed stub used to
materialise on a full discover only. Editing a lint marks the lint dirty and
leaves the governed ``__init__.py`` clean, and the merge dropped any re-run
source's node whose ``props.file`` was not itself dirty -- including one for an
ID the graph did not hold at all. Dropping ``props.file`` here would have
sidestepped the guard at the cost of a ``file:`` node that cannot say which
file it is; the guard was fixed instead, in
``weld._discover_node_merge.incremental_claim_wins``.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from pathlib import Path

from weld._node_ids import file_id
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult
from weld.strategies._provenance import file_provenance
from weld.strategies._python_anchor import module_exports, yields_file_anchor
from weld.strategies._target_ids import target_ids

#: Bounded, non-nested pattern for repo-relative path literals. Must contain a
#: recognizable extension; match length is capped so a hostile constant cannot
#: make the scan do unbounded work. Mirrors the shape used by
#: ``concept_from_bd._PATH_PATTERN``.
_PATH_PATTERN = re.compile(
    r"\b([A-Za-z0-9_][A-Za-z0-9_./*-]{0,120}"
    r"\.(?:py|pyi|md|sh|yaml|yml|toml|json|jsonl|bzl|bazel|cfg|ini|txt))\b"
)

#: Strings longer than this are not scanned. Real path-bearing constants and
#: module docstrings are far below it; the cap bounds regex work on inputs
#: that are prose, embedded data, or hostile.
_MAX_STRING_LEN = 20_000

#: Maximum distinct path literals harvested from one module.
_MAX_LITERALS_PER_MODULE = 200

#: A glob literal expanding past this is dropped whole: it describes
#: extension-wide governance, not a per-file relationship.
_MAX_GLOB_EXPANSION = 25

#: Ceiling on distinct glob literals *resolved* in one extract() call.
#: Resolving a ``**`` literal costs a full tree walk (measured: ~0.4s over
#: 1100 files), and the per-module literal cap alone would permit 200 walks
#: per validator -- half an hour of wall clock on a mid-sized repo, from a
#: single hostile or careless module. Discovery runs against repositories
#: weld did not write, so the walk count is bounded globally rather than
#: per module. Repeated literals cost nothing: they are memoized, and only
#: a genuine resolution draws down the budget. This repo's validators name
#: five distinct glob literals in total, so the ceiling has an order of
#: magnitude of headroom before it can affect a real result.
_MAX_GLOB_RESOLUTIONS = 50


def _string_constants(tree: ast.AST) -> list[str]:
    """Return every string constant in *tree*, docstrings included.

    Docstrings are deliberately in scope. The path a lint governs is as
    often stated in its prose ("weld's two guarded entry points work
    because ``weld/__init__.py`` is empty") as in a module constant, and
    the existence check downstream is what keeps prose from inventing
    edges to files that are not there.
    """
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if len(node.value) <= _MAX_STRING_LEN:
                out.append(node.value)
    return out


def _path_literals(strings: list[str]) -> list[str]:
    """Return deduplicated path-shaped literals found across *strings*.

    The result is sorted, so downstream edge order does not depend on AST
    walk order (ADR 0012 §3 canonical output). The cap is applied during
    the scan rather than after it, so *which* literals survive a
    cap-tripping module does depend on walk order -- deterministic for a
    given tree, which is what the canonical-output rule requires.
    """
    found: set[str] = set()
    for text in strings:
        for match in _PATH_PATTERN.finditer(text):
            found.add(match.group(1))
            if len(found) >= _MAX_LITERALS_PER_MODULE:
                return sorted(found)
    return sorted(found)


def _safe_direct_path(root: Path, literal: str) -> str | None:
    """Return the repo-relative path for a non-glob *literal*, or None.

    Rejects absolute paths, ``..`` traversal, symlinks, anything that is
    not a regular file, and anything that resolves outside *root*.
    """
    if literal.startswith(("/", "\\")):
        return None
    if ".." in Path(literal).parts:
        return None
    full = root / literal
    try:
        if full.is_symlink() or not full.is_file():
            return None
        resolved = full.resolve()
        root_resolved = root.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(root_resolved):
        return None
    return literal.replace("\\", "/")


def _resolve_literal(root: Path, literal: str) -> list[str]:
    """Return the repo-relative paths *literal* names, bounded and sanitized."""
    if "*" not in literal:
        direct = _safe_direct_path(root, literal)
        return [direct] if direct else []
    if literal.startswith(("/", "\\")) or ".." in Path(literal).parts:
        return []
    try:
        matched = resolve_glob(root, literal)
    except (OSError, ValueError):
        return []
    if len(matched) > _MAX_GLOB_EXPANSION:
        return []
    out: list[str] = []
    for path in matched:
        try:
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        out.append(rel)
    return sorted(out)


class _LiteralResolver:
    """Memoized, budget-bounded resolution of path literals for one run.

    The memo makes repeated literals free across validator modules; the
    budget bounds how many *fresh* glob walks the whole extract may pay
    for. See :data:`_MAX_GLOB_RESOLUTIONS`.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._memo: dict[str, list[str]] = {}
        self._glob_budget = _MAX_GLOB_RESOLUTIONS

    def resolve(self, literal: str) -> list[str]:
        """Return the repo-relative paths *literal* names."""
        cached = self._memo.get(literal)
        if cached is not None:
            return cached
        if "*" in literal:
            if self._glob_budget <= 0:
                self._memo[literal] = []
                return []
            self._glob_budget -= 1
        resolved = _resolve_literal(self._root, literal)
        self._memo[literal] = resolved
        return resolved


def _stub_node(root: Path, rel_path: str) -> dict | None:
    """Return a minimal ``file:`` node for *rel_path*, or None if unwarranted.

    Only Python files that ``python_module`` would never anchor are minted:
    an export-less ``__init__.py`` is invisible to the connected structure by
    design, yet is exactly the kind of file a validator governs.

    The anchor question is asked through the shared ``_python_anchor`` rule
    rather than restated here, and the parse is done locally so the two
    reasons ``python_module`` emits nothing stay distinguishable. A file it
    skipped because it does not *parse* gets no stub: minting a node for
    source weld cannot read would put a file in the graph that no query can
    say anything true about.
    """
    if not rel_path.endswith(".py"):
        return None
    try:
        tree = ast.parse((root / rel_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return None
    if yields_file_anchor(Path(rel_path).name, module_exports(tree)):
        return None
    return {
        "type": "file",
        "label": Path(rel_path).name,
        "props": {
            "file": rel_path,
            "source_strategy": "validator_targets",
            "authority": "derived",
            "confidence": "inferred",
            # ``implementation`` is what ``python_module`` stamps on every
            # file node it anchors; the stub is the same kind of artifact,
            # only export-less. ``ROLE_VALUES`` has no governance member and
            # inventing one here would fail the contract validator.
            "roles": ["implementation"],
            "origin": "project",
        },
    }


def _selected(paths: list[Path], include_names: list[str]) -> list[Path]:
    """Return *paths* filtered by basename against *include_names* patterns."""
    if not include_names:
        return paths
    return [
        p for p in paths
        if any(fnmatch.fnmatch(p.name, pat) for pat in include_names)
    ]


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Emit ``validates`` edges from validator modules; see module docstring."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob")
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)
    excludes = source.get("exclude", []) or []
    raw_names = source.get("include_names") or []
    include_names = [str(n) for n in raw_names] if isinstance(raw_names, list) else []

    matched = _selected(resolve_glob(root, pattern, excludes), include_names)
    # ``discovered_from`` is the matched files themselves. Deriving it from
    # their parents avoided the ``(root / pattern).parent`` trap the sibling
    # single-directory strategies fell into -- that one silently yields
    # nothing for a ``**`` pattern, since ``root/tools/**`` is not a
    # directory -- but kept the other half of the same bug: a match sitting
    # at the repo root has ``.`` for a parent, so the entry became ``"./"``,
    # the marker that makes every path in the repository count as tracked
    # source (bd od2a).
    discovered_from.extend(file_provenance(root, matched))

    resolver = _LiteralResolver(root)
    for path in sorted(matched):
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
            continue
        try:
            rel_self = path.relative_to(root).as_posix()
        except ValueError:
            continue
        validator_id = file_id(rel_self)

        governed: set[str] = set()
        for literal in _path_literals(_string_constants(tree)):
            for rel_target in resolver.resolve(literal):
                if rel_target != rel_self:
                    governed.add(rel_target)

        for rel_target in sorted(governed):
            stub = _stub_node(root, rel_target)
            if stub is not None:
                nodes.setdefault(file_id(rel_target), stub)
            for target_id in target_ids(rel_target):
                edges.append(
                    {
                        "from": validator_id,
                        "to": target_id,
                        "type": "validates",
                        "props": {
                            "source_strategy": "validator_targets",
                            "authority": "derived",
                            "confidence": "inferred",
                            # ADR 0074 (sixth amendment): the validator this
                            # edge was scanned from, never the governed
                            # target -- a governed target routinely lives in
                            # a disjoint source entry (a tool_script-owned
                            # .sh file, a markdown doc, ...), so without
                            # this stamp an incremental refresh that dirties
                            # only the target purges the edge by endpoint
                            # membership and never re-mints it, because this
                            # validator's own file never changed.
                            "provenance": {"file": rel_self},
                        },
                    }
                )

    return StrategyResult(nodes, edges, discovered_from)
