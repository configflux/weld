"""Strategy: surface a static web frontend (HTML/CSS/JS) as queryable file nodes.

The bundled language strategies (``python_module``, ``typescript_exports``,
``tree_sitter`` and friends) do not parse hand-written HTML/CSS or plain
browser ``.js`` chrome, so a static single-page frontend like
``weld/viz/static/{index.html,app.js,styles.css}`` is invisible to the
connected structure. A question about the visualizer inspector's DOM/CSS
structure (``wd query "inspector tabs changes"`` / ``"inspector grid"``)
therefore returned only fuzzy token matches on the word ``changes`` in
unrelated Python tests.

This strategy walks the configured glob and emits one ``file`` node per
matched HTML/CSS/JS file (existing vocabulary -- no new node type), with
the file's meaningful entity NAMES lifted into ``props.headings`` -- the
same indexed searchable channel the ``markdown`` strategy uses to make H2
section text queryable (see :mod:`weld.query_index`). The extracted names
are:

- HTML: element ``id`` attributes (``#inspector``, ``#inspect-tabs``,
  ``#inspect-body``, ``#inspect-changes``, ``#trace-tray`` ...) and the
  ``class`` tokens on structural containers.
- CSS: rule selectors at the start of a declaration block (``.inspector``,
  ``.inspect-tabs``, ``#inspect-kind`` ...).
- JS: top-level ``function`` declaration names (``setActiveTab``,
  ``loadDiff``, ``renderChangesPanel``, ``showNode`` ...).

The nodes are query leaves: no ``contains`` edges are emitted, so the
file-anchor-symmetry invariant (ADR 0041 Layer 3) is never engaged. The
strategy only reads the matched files (bounded by the repo boundary and
the shared exclusion policy) and never executes anything.
"""

from __future__ import annotations

import re
from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id
from weld.strategies._helpers import (
    StrategyResult,
    filter_glob_results,
    should_skip,
)

#: Suffix -> role. HTML carries documentation-shaped structure (the DOM),
#: CSS/JS are implementation chrome. All three keep the generic ``file``
#: node type; the role only colours the node.
_ROLE_BY_SUFFIX: dict[str, str] = {
    ".html": "doc",
    ".htm": "doc",
    ".css": "implementation",
    ".js": "implementation",
    ".mjs": "implementation",
}

#: ``id="..."`` / ``id='...'`` attributes on any HTML element. Captures the
#: bare id; the ``#``-prefixed form is added alongside so both ``inspector``
#: and ``#inspector`` tokenize for query (``_split_field`` strips ``#``).
_HTML_ID_RE = re.compile(r"""\bid\s*=\s*["']([A-Za-z][\w:-]*)["']""")

#: ``class="a b c"`` attributes. Each whitespace-separated token becomes a
#: heading entry so structural container classes (``inspector``,
#: ``inspect-tabs`` ...) are queryable even when they carry no id.
_HTML_CLASS_RE = re.compile(r"""\bclass\s*=\s*["']([^"']+)["']""")

#: A CSS rule selector list at the start of a line, immediately preceding
#: the opening ``{`` of its declaration block. Matches ``.foo``, ``#bar``,
#: ``.a, .b`` and bare element selectors. At-rules (``@media`` ...) are
#: skipped by the leading-token guard below.
_CSS_RULE_RE = re.compile(r"^\s*([.#]?[A-Za-z][^{}/@]*?)\s*\{", re.MULTILINE)

#: ``#id`` / ``.class`` selector tokens inside a CSS selector list.
_CSS_SELECTOR_TOKEN_RE = re.compile(r"[.#][A-Za-z][\w-]*")

#: ``display: <value>`` declarations. The value names the layout model
#: (``grid``, ``flex``, ``inline-grid`` ...), which is the structural
#: vocabulary a reader asks about ("inspector grid"). Captured as a bare
#: keyword so the layout model is queryable alongside the selectors.
_CSS_DISPLAY_RE = re.compile(r"\bdisplay\s*:\s*([a-z][a-z-]*)", re.IGNORECASE)

#: ``grid-*`` / ``flex-*`` shorthand property names (and the bare
#: ``grid`` / ``flex`` properties). Reduced to the family keyword so the
#: layout family surfaces without flooding the index with every property.
_CSS_LAYOUT_PROP_RE = re.compile(r"\b(grid|flex)(?:-[a-z]+)?\s*:", re.IGNORECASE)

#: Top-level (column-0) ``function name(`` / ``async function name(``
#: declarations. Method shorthands and nested closures are intentionally
#: out of scope -- the goal is the public handler surface, not a call graph.
_JS_FUNC_RE = re.compile(
    r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", re.MULTILINE
)

#: Cap on headings per file so a pathological asset cannot bloat the index.
#: Real viz files carry well under this; the cap is a safety rail.
_MAX_HEADINGS = 400


def _dedupe_preserving(items: list[str]) -> list[str]:
    """Return *items* with duplicates removed, first occurrence kept."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _extract_html(text: str) -> list[str]:
    """Return queryable entity names for an HTML document.

    Emits element ids in both bare (``inspector``) and anchor
    (``#inspector``) form plus class tokens, so a query for the inspector
    DOM resolves whether the user types the id with or without ``#``.
    """
    names: list[str] = []
    for ident in _HTML_ID_RE.findall(text):
        names.append(ident)
        names.append("#" + ident)
    for class_attr in _HTML_CLASS_RE.findall(text):
        names.extend(tok for tok in class_attr.split() if tok)
    return _dedupe_preserving(names)[:_MAX_HEADINGS]


def _extract_css(text: str) -> list[str]:
    """Return CSS rule selectors plus layout keywords as entity names.

    Each declaration-block selector list is split into its ``.class`` /
    ``#id`` tokens. Bare element selectors (``body``, ``html``) carry no
    domain signal and are dropped. The layout model is also surfaced --
    ``display`` values (``grid``, ``flex`` ...) and ``grid``/``flex``
    family property names -- so a structural query like ``"inspector
    grid"`` resolves to the stylesheet that lays the inspector out as a
    grid, not just to its selector name.
    """
    names: list[str] = []
    for selector_list in _CSS_RULE_RE.findall(text):
        head = selector_list.strip()
        if not head or head.startswith("@"):
            continue
        names.extend(_CSS_SELECTOR_TOKEN_RE.findall(selector_list))
    names.extend(v.lower() for v in _CSS_DISPLAY_RE.findall(text))
    names.extend(v.lower() for v in _CSS_LAYOUT_PROP_RE.findall(text))
    return _dedupe_preserving(names)[:_MAX_HEADINGS]


def _extract_js(text: str) -> list[str]:
    """Return top-level JS function names as queryable entity names."""
    return _dedupe_preserving(_JS_FUNC_RE.findall(text))[:_MAX_HEADINGS]


_EXTRACTORS = {
    ".html": _extract_html,
    ".htm": _extract_html,
    ".css": _extract_css,
    ".js": _extract_js,
    ".mjs": _extract_js,
}


def _build_node(rel: Path, text: str) -> tuple[str, dict] | None:
    """Build the ``(node_id, node_dict)`` pair for a matched frontend file.

    Returns ``None`` for suffixes the strategy does not understand so the
    caller can skip a stray file that matched the glob but is not a
    supported web asset.
    """
    suffix = rel.suffix.lower()
    extractor = _EXTRACTORS.get(suffix)
    if extractor is None:
        return None
    role = _ROLE_BY_SUFFIX.get(suffix, "implementation")
    headings = extractor(text)
    props: dict = {
        "file": rel.as_posix(),
        "kind": "frontend",
        "roles": [role],
        "source_strategy": "viz_frontend",
        "authority": "derived",
        "confidence": "definite",
        "origin": "project",
    }
    if headings:
        props["headings"] = headings
    nid = _canonical_file_id(rel.as_posix())
    return nid, {"type": "file", "label": rel.name, "props": props}


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Emit one ``file`` node per matched HTML/CSS/JS asset.

    Each node carries the file's entity names in ``props.headings`` (the
    indexed query channel). No edges are produced, so there is nothing for
    :func:`weld._discover_postprocess._clean_and_dedup_edges` to prune and
    the file-anchor-symmetry rule does not apply.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    excludes = source.get("exclude", []) or []
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    parent = (root / pattern).parent
    if not parent.is_dir():
        return StrategyResult(nodes, edges, discovered_from)

    matched = filter_glob_results(
        root, sorted(parent.glob(Path(pattern).name)), excludes=excludes
    )
    for path in matched:
        if not path.is_file():
            continue
        if should_skip(path, excludes, root=root):
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        built = _build_node(rel, text)
        if built is None:
            continue
        nid, node = built
        nodes[nid] = node
        discovered_from.append(rel.as_posix())

    return StrategyResult(nodes, edges, discovered_from)
