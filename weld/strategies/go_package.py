"""Strategy: ``package:go:<import_path>`` nodes for Go source directories.

Closes the Go half of the gap ``python_package``/``csharp_package`` (ADR
0041 Layer 3 ``file-anchor-symmetry``, ADR 0060) already close for Python
and C#. The shared tree-sitter strategy promotes every Go function,
method, and type declaration to a ``symbol:go:*`` node
(``weld/strategies/_ts_definitions.py``'s ``promote_definition_symbols``,
unconditional across every tree-sitter language) and stamps an outgoing
``contains`` edge from the owning ``file:*`` node. Nothing minted an
inbound edge for Go, so any Go file with at least one declaration tripped
``file-anchor-symmetry`` -- verified empirically against the bundled
``weld/tests/fixtures/tier1/go/sample_go`` fixture: 2 live violations
before this strategy existed (``file:shapes/shapes``,
``file:geometry/geometry_test``). This module is the missing upstream
parent, mirroring ``python_package``/``csharp_package``.

It is also the producer half ``weld.cross_repo.package_import_resolver``
needs (bd 1wcjp, following bd bt5m's diagnosis): before this strategy, Go
minted zero ``type=package`` nodes from a child's own source, so the
resolver had nothing genuine to match a sibling's ``imports_from``
against. The only ``package`` node a Go child ever carried came from
``weld/graph_closure.py``'s ``_ensure_package_node`` -- a *consumer*-side
placeholder synthesised for that same child's own unresolved import
(``props.authority == "external"``), not a producer declaration. This
strategy mints the real thing instead.

Design
------

Go's package model is simpler than C#'s: every directory with ``.go``
files is exactly one package, and its import path is always
``<module_path>`` (repo root) or ``<module_path>/<relative_dir>`` --
never a name declared inside the file. The ``package <name>`` clause
picks the *local* identifier a file uses to refer to its own package,
not the string other code imports (a directory's import path is fixed by
its position under the module root, independent of that identifier). So,
unlike ``csharp_package``, this strategy never parses a package clause
for grouping: it reads only ``go.mod`` (via the same
:func:`weld.strategies._go_tree_sitter.load_module_path` the shared
tree-sitter strategy already calls) plus the on-disk directory layout.

- Walk the configured ``glob`` for ``.go`` files.
- Group matched files by their containing directory.
- Skip a group unless at least one member file has a top-level ``func``
  or ``type`` declaration (the exact shape ``weld/languages/go.yaml``'s
  ``exports`` tree-sitter query captures) --
  :func:`_has_go_declaration`, the Go analogue of ``python_package``'s
  ``_has_anchoring_member``: a directory whose files own no promotable
  declaration never gets a ``file:`` node from the shared tree-sitter
  pass either, so minting a package node for it would leave a
  zero-edge orphan the first full discover ever produces (bd g7rs).
- Mint one ``package:go:<import_path>`` node per surviving directory and
  one ``contains`` edge to every member file (mirrors ``python_package``:
  a member that itself produces no ``file:`` node, e.g. a lone helper
  with only unexported ``var``s, is a dangling edge the post-processing
  edge cleanup already removes
  (``weld/_discover_postprocess.py``'s ``_clean_and_dedup_edges``); only
  a *wholly* edgeless node is the failure mode this strategy avoids).
- No ``package:`` config override (unlike ``python_package``'s
  ``tools/`` case): Go's directory-to-import-path rule has no ambiguous
  case that needs one.

Determinism (ADR 0012 § 3): file lists are sorted, one node is emitted
per directory, and the ``contains`` edge list is sorted by destination
ID before emission so repeated runs produce byte-identical graphs.

Origin (ADR 0042): every import path minted here is classified
``project`` unconditionally -- the strategy only ever walks globs inside
the workspace, so everything it discovers is first-party by
construction, exactly as ``python_package``/``csharp_package`` already
reason for their own project-rooted nodes.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id
from weld._node_ids import package_id as _canonical_package_id
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._go_tree_sitter import load_module_path
from weld.strategies._helpers import StrategyResult
from weld.strategies._provenance import directory_provenance

_STRATEGY = "go_package"

#: A top-level Go declaration always starts at column 0 after gofmt --
#: Go has no nested top-level scope, so this is precise for any
#: conventionally-formatted source (virtually all real Go code). Matches
#: exactly the shape ``weld/languages/go.yaml``'s ``exports`` query
#: captures (``function_declaration``, ``method_declaration``, and
#: ``type_declaration`` all start with one of these two keywords).
_DECL_RE = re.compile(r"(?m)^(?:func|type)\b")


def _read_text_safely(path: Path) -> str:
    """Return file text or empty string on read error.

    Mirrors ``csharp_package._read_text_safely``: discovery never
    crashes on unreadable bytes.
    """
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _has_go_declaration(text: str) -> bool:
    """Return True if *text* has >=1 top-level ``func``/``type`` decl."""
    return bool(_DECL_RE.search(text))


def _import_path(module_path: str, rel_dir: str) -> str:
    """Return the Go import path for *rel_dir* under *module_path*.

    ``""``/``"."`` (repo root) maps to the bare module path; any other
    directory appends as a POSIX suffix. This is the one rule Go's
    import resolution actually uses -- never the ``package`` clause
    identifier declared inside a file.
    """
    if not rel_dir or rel_dir == ".":
        return module_path
    return f"{module_path}/{rel_dir}"


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Emit ``package:go:<import_path>`` nodes plus ``contains`` edges.

    Source schema:

    ``glob`` (required)
        Path glob for Go sources, e.g. ``"**/*.go"``. Same semantics as
        the Go ``tree_sitter`` source this strategy is meant to pair
        with.
    ``exclude`` (optional)
        List of patterns passed to ``resolve_glob`` (same semantics as
        every other glob-driven strategy in the family).
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    excludes = source.get("exclude") or []

    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    module_path = load_module_path(root)
    if not module_path:
        # No go.mod (or an unparseable one): no import path can be
        # constructed for anything this glob matches. Mirrors
        # csharp_package's "no detectable namespace -> skip".
        return StrategyResult(nodes, edges, discovered_from)

    matched: list[Path] = resolve_glob(root, pattern, excludes)
    if not matched:
        return StrategyResult(nodes, edges, discovered_from)

    # Group files by their containing directory -- Go's package
    # boundary is always a directory, never a name declared in source.
    by_dir: dict[str, list[Path]] = defaultdict(list)
    for go_file in sorted(matched):
        try:
            rel = go_file.relative_to(root)
        except ValueError:
            continue
        rel_dir = rel.parent.as_posix()
        if rel_dir == ".":
            rel_dir = ""
        by_dir[rel_dir].append(go_file)

    for rel_dir in sorted(by_dir.keys()):
        files = by_dir[rel_dir]
        # A directory that contributes no promotable declaration gets no
        # package node: the shared tree-sitter pass would emit no
        # file: node for any of its members either, so the contains
        # edges below would all dangle and the node would land as a
        # zero-edge orphan (bd g7rs; mirrors python_package's
        # _has_anchoring_member).
        if not any(_has_go_declaration(_read_text_safely(f)) for f in files):
            continue

        import_path = _import_path(module_path, rel_dir)
        pkg_nid = _canonical_package_id("go", import_path)
        # Idempotent merge: a second glob entry over the same directory
        # re-derives identical props.
        nodes[pkg_nid] = {
            "type": "package",
            "label": import_path,
            "props": {
                "name": import_path,
                "language": "go",
                "dir": rel_dir,
                "source_strategy": _STRATEGY,
                "authority": "derived",
                "confidence": "definite",
                "roles": ["package"],
                # ADR 0042: only the workspace is walked here, so every
                # import path minted by this strategy is first-party.
                "origin": "project",
            },
        }
        discovered_from.extend(directory_provenance(root, rel_dir, files))

        # Sort children by canonical file ID so the edge list is
        # byte-identical across runs.
        children: list[tuple[str, str]] = []
        for go_file in files:
            rel_path = go_file.relative_to(root).as_posix()
            file_nid = _canonical_file_id(rel_path)
            children.append((file_nid, rel_path))
        children.sort()

        for file_nid, _rel_path in children:
            edges.append(
                {
                    "from": pkg_nid,
                    "to": file_nid,
                    "type": "contains",
                    "props": {
                        "source_strategy": _STRATEGY,
                        "confidence": "definite",
                    },
                }
            )

    return StrategyResult(nodes, edges, sorted(set(discovered_from)))
