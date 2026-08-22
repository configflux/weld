"""Load-time inverted index for fast wd query candidate lookup.

Builds a token-to-node-IDs mapping from node ID, label, props.file,
props.exports, props.qualname, props.constants, props.description,
props.summary, props.headings and props.keywords so that
``Graph.query()`` can narrow candidates in O(1) per token instead of
scanning every node linearly.

The index is a plain ``dict[str, set[str]]`` — no external files or
databases.  It is built once at graph load time and kept in sync by
incremental ``index_node`` / ``deindex_node`` calls on mutations.

"""

from __future__ import annotations

#: Characters the index treats as separators when splitting an indexed
#: field. The single source of truth for the index's separator alphabet
#: (bd 2xoj) -- ``weld.synonyms._separator_variants`` imports this rather
#: than re-declaring its own subset, which is exactly how the two drifted
#: apart before: the index split six characters, the query side re-spelled
#: only two of them, so a query token spelled with ``.``, ``/``, ``:`` or
#: ``·`` could only match a node carrying that exact punctuation verbatim.
SEPARATOR_CHARS = "/:.·-_"

_SEPARATORS = str.maketrans(SEPARATOR_CHARS, " " * len(SEPARATOR_CHARS))

def _split_field(value: str) -> list[str]:
    """Lowercase *value* and split on common separators."""
    lowered = value.lower()
    parts = lowered.translate(_SEPARATORS).split()
    tokens = [lowered]
    tokens.extend(p for p in parts if p)
    return tokens

def node_tokens(nid: str, node: dict) -> list[str]:
    """Extract lowercased tokens from a node for indexing.

    Sources: node ID, label, props.file, props.exports, props.qualname,
    props.constants, props.description, props.summary, props.headings,
    props.keywords.

    ``props.constants`` carries module-level Python constants
    (``UPPER_CASE`` / ``_UPPER_CASE``) emitted by the ``python_module``
    strategy. Indexing them here is what makes ``wd query <CONSTANT>``
    return the file node that owns the constant. The constant slice is
    upstream-bounded by ``weld.file_index`` to keep the index small.

    ``props.headings`` carries H2/H3 heading text emitted by the
    ``markdown`` strategy so the inverted index can narrow candidates
    by heading content for multi-token doc queries (e.g. ``wd query
    "language support"`` matching ``## Language support``).

    ``props.summary`` carries the opening paragraph of a module's own
    docstring (bd ph1g) -- the author's one-line statement of what the
    module is, recorded by ``python_module`` on every discover. It is
    what makes a name that appears *only* in that sentence reachable:
    ``weld/serializer.py`` opens "Canonical serializer for
    ``graph.json``", and before this field no node in the graph carried
    the substring ``graph.json`` at all, so the token matched nothing.
    Distinct from ``description`` on purpose -- that field is enrichment
    *output*, written by an LLM pass, while a summary is structural
    input re-read from source every run.

    ``props.keywords`` is the generic, strategy-owned version of that
    (ADR 0105): any strategy may declare short words that should reach
    the fact it recorded, without a core edit here. It exists because
    the alternative was misusing ``headings`` -- ``viz_frontend``
    already files HTML element ids and CSS selectors under it, and a
    build target's rule kind would have been the third thing named a
    heading that is not one. Kept short by contract; prose belongs in
    ``description``, since ``candidate_nodes`` scans every indexed
    token per query token and index size is on the query hot path.
    """
    tokens: list[str] = _split_field(nid)

    label = node.get("label", "")
    if label:
        tokens.extend(_split_field(label))

    props = node.get("props") or {}

    file_val = props.get("file") or ""
    if file_val:
        tokens.extend(_split_field(file_val))

    for exp in props.get("exports", []):
        if isinstance(exp, str):
            tokens.append(exp.lower())

    qualname_val = props.get("qualname") or ""
    if qualname_val:
        tokens.extend(_split_field(str(qualname_val)))

    for const in props.get("constants", []):
        if isinstance(const, str):
            tokens.extend(_split_field(const))

    desc_val = props.get("description") or ""
    if desc_val:
        tokens.extend(_split_field(desc_val))

    summary_val = props.get("summary") or ""
    if summary_val:
        tokens.extend(_split_field(str(summary_val)))

    for heading in props.get("headings", []):
        if isinstance(heading, str) and heading:
            tokens.extend(_split_field(heading))

    for keyword in props.get("keywords", []):
        if isinstance(keyword, str) and keyword:
            tokens.extend(_split_field(keyword))

    return tokens

def build_index(nodes: dict[str, dict]) -> dict[str, set[str]]:
    """Build a complete inverted index from *nodes*.

    Returns a dict mapping each lowercased token to the set of node IDs
    whose fields contain that token.
    """
    index: dict[str, set[str]] = {}
    for nid, node in nodes.items():
        _add_node(index, nid, node)
    return index

def _add_node(index: dict[str, set[str]], nid: str, node: dict) -> None:
    """Add one node's tokens to *index*."""
    for token in node_tokens(nid, node):
        if token not in index:
            index[token] = set()
        index[token].add(nid)

def index_node(index: dict[str, set[str]], nid: str, node: dict) -> None:
    """Incrementally add a node to *index*."""
    _add_node(index, nid, node)

def deindex_node(index: dict[str, set[str]], nid: str) -> None:
    """Remove *nid* from every entry in *index*."""
    empty: list[str] = []
    for token, node_ids in index.items():
        node_ids.discard(nid)
        if not node_ids:
            empty.append(token)
    for token in empty:
        del index[token]

def candidate_nodes(
    index: dict[str, set[str]],
    tokens: list[str],
) -> set[str] | None:
    """Return candidate node IDs that might match all *tokens*.

    For each query token, collects every node ID whose indexed tokens
    contain the query token as a substring, then intersects across all
    query tokens.  Returns ``None`` when the index is empty (caller
    should fall back to a full scan).
    """
    if not index:
        return None
    candidates: set[str] | None = None
    for tok in tokens:
        tok_hits: set[str] = set()
        for indexed_token, node_ids in index.items():
            if tok in indexed_token:
                tok_hits |= node_ids
        if candidates is None:
            candidates = tok_hits
        else:
            candidates &= tok_hits
        if not candidates:
            return set()
    return candidates
