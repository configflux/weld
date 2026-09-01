"""Strategy: Generic markdown doc nodes with doc_kind authority tagging.

Reads ``doc_kind`` from the source config to tag each doc node with its
kind (adr, policy, runbook, guide, gate, verification).  When ``doc_kind``
is absent, falls back to inferring from ``id_prefix``.

When ``extract_sections`` is true in the source config, the strategy also
parses H2 headings to emit section-level nodes with ``span`` and
``section_kind`` metadata.  Section nodes are linked to their parent doc
via ``contains`` edges.

Authority mapping:
- adr, policy, runbook, gate, verification -> canonical
- guide (and fallback)                     -> derived

Per ADR 0074 (second amendment) every ``relates_to`` edge carries
``props.provenance.file``: the **mentioning** markdown file this strategy
walked, never the target it resolved. Stamping the target would be
backwards -- the target is the endpoint that goes stale in the very case
the stamp exists to survive.

Unlike ``test_peer`` (bd heum) this edge is not *currently* at risk, and
the stamp is deliberate belt-and-braces (bd 41vw). An edge is only
emitted when the target resolves inside ``path_to_nid``, i.e. would
itself be minted by this same ``extract`` call, so the producing file and
both endpoints always share one glob; purging either endpoint implies a
dirty file in this strategy's own source entry, which re-runs it over the
*whole* glob and re-mints every edge. heum needed *disjoint* globs, which
cannot arise here. But that safety is incidental -- it rests on
re-reading every matched ``.md`` whenever any one is dirty, exactly the
cost ADR 0084 removed for ``python_module`` via dirty scope, and the same
optimisation here would silently reintroduce heum. The stamp makes the
retention contract explicit rather than emergent;
``incremental_markdown_provenance_purge_test`` pins both halves.

A second, unrelated edge pass (ADR 0128, bd ziv1) mints ``documents``
edges from the doc node to any ``file:*`` node a backtick-quoted ``.py``
path or dotted-module citation in the body resolves to -- see
:mod:`weld.strategies._markdown_code_refs` for the match rule. Unlike the
``relates_to`` pass above, this one is *always* cross-source (the doc
family and the code family are different ``.weld/discover.yaml``
entries), so the provenance stamp there is load-bearing, not
belt-and-braces.
"""

from __future__ import annotations

import re
from pathlib import Path

from weld._rel_path import rel_to_root
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult
from weld.strategies._markdown_code_refs import code_reference_edges
from weld.strategies._markdown_fence import content_text, first_h1, iter_headings
from weld.strategies._markdown_section_kind import classify_section

#: doc_kind values that represent authoritative/primary guidance.
_CANONICAL_KINDS: frozenset[str] = frozenset(
    ["adr", "policy", "runbook", "gate", "verification"]
)

#: Map id_prefix fragments to doc_kind for backward-compatible inference.
_PREFIX_TO_KIND: dict[str, str] = {
    "adr": "adr",
    "policy": "policy",
    "runbook": "runbook",
    "gate": "gate",
    "verification": "verification",
    "guide": "guide",
}

# -- Inter-doc link extraction ----------------------------------------------
# Matches inline markdown links of the form ``[text](path.md)`` or
# ``[text](path.md#anchor)``. We deliberately restrict the target to
# end in ``.md`` (with an optional ``#anchor`` fragment) to avoid
# emitting edges to unrelated assets (images, code, external URLs).
# Reference-style links ``[text][ref]`` and angle-bracket autolinks are
# intentionally out of scope; cross-doc graph signal does not require
# them today.
#
# Edge type is ``relates_to`` (the existing generic "this artifact mentions
# that one" vocabulary; see ``concept_from_bd`` for the same precedent).
# Using an existing contract type avoids a vocabulary expansion that would
# require an ADR.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s#]+\.md)(#[^)\s]*)?\)")

def _infer_doc_kind(id_prefix: str) -> str:
    """Infer doc_kind from the id_prefix when not explicitly configured."""
    for fragment, kind in _PREFIX_TO_KIND.items():
        if fragment in id_prefix:
            return kind
    return "guide"

def _slugify(text: str) -> str:
    """Convert heading text to a URL-safe slug for node IDs."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")

def _parse_sections(text: str) -> list[dict]:
    """Parse H2 headings from markdown text and return section metadata.

    Returns a list of dicts with keys: heading, slug, start_line, end_line,
    section_kind (may be None).

    Only extracts H2 headings (## ...) as section boundaries.  H1 is the
    doc title, H3+ are subsections within an H2 and are not promoted to
    separate nodes.

    Headings inside fenced code blocks are skipped (bd ve41): a ``##`` in a
    markdown sample is not a section boundary, and treating it as one ends
    the enclosing real section mid-sample. ``lines`` is still the whole
    document, so the numbers below stay document line numbers.
    """
    lines = text.splitlines()
    sections: list[dict] = []

    for i, level, heading in iter_headings(text):
        if level != 2:
            continue
        sections.append({
            "heading": heading,
            "slug": _slugify(heading),
            "start_line": i + 1,  # 1-indexed
            "end_line": -1,  # filled in below
            "section_kind": classify_section(heading),
        })

    # Fill in end_line for each section (up to the next section or EOF)
    for idx, sec in enumerate(sections):
        if idx + 1 < len(sections):
            sec["end_line"] = sections[idx + 1]["start_line"] - 1
        else:
            sec["end_line"] = len(lines)

    return sections


def _collect_heading_texts(text: str) -> list[str]:
    """Return sorted-deduped H2 and H3 heading texts from *text*.

    Closes the 2026-05-15 ``wd query 'language support'`` dogfood gap:
    doc nodes carry only filename-derived label and path metadata, so
    multi-token queries that match section headings (e.g. ``## Language
    support``) but no node field surface nothing. Emitting headings as a
    sorted-deduped list lets the inverted index (``query_index.node_tokens``)
    and the runtime match surface (``Graph._match_token_groups``) tokenize
    heading words without changing the file index or ranking pipeline.

    H1 is the doc title and is usually a filename restatement -- skipped
    here so it does not dominate the token list with low-signal words.
    H2/H3 are the section structure that user queries actually target.

    Headings inside fenced code blocks are skipped (bd ve41): a doc that
    *shows* markdown was feeding the index vocabulary from its own templates,
    so ``docs/release.md`` answered a query for "Added" with a changelog
    placeholder rather than a document that has an Added section.
    """
    found: set[str] = set()
    for _index, level, heading in iter_headings(text):
        if level in (2, 3) and heading:
            found.add(heading)
    return sorted(found)


def _doc_label(md: Path, text: str | None, id_prefix: str) -> str:
    """The node label for doc file *md*: its filename, or its title.

    A doc's filename normally *is* its title (``platform-overview.md`` ->
    "Platform Overview"), so the stem is the label and the H1 is skipped as a
    restatement. ``README.md`` is the one filename that says nothing about the
    document: it is a placement convention, and the file it names is usually
    the index of the repository around it. Labelling such a node "Readme" left
    it unreachable by the only term anyone would search for -- a docs
    repository whose README declares ``# Platform Documentation`` answered that
    query with a *different* document (field eval v0.24.0 N8). So a README
    takes its H1 as its label when it has one, and falls back to the stem when
    it does not. Node ids stay stem-derived either way, so nothing that
    references a doc node has to change.
    """
    if md.name == "README.md" and text is not None:
        title = first_h1(text)
        if title:
            return title
    if "runbook" in id_prefix:
        return md.stem.replace("_", " ").title()
    return md.stem.replace("-", " ").title()


def _extract_md_link_targets(text: str) -> list[tuple[str, str]]:
    """Return ``(href_without_anchor, anchor_or_empty)`` per markdown link.

    Order is preserved for caller-side dedupe stability. Only ``.md``
    targets are returned (other links are filtered by the regex).

    Fenced blocks are removed first, so a link that only ever renders as code
    mints no edge -- see :func:`content_text` for the trade (bd w624).
    """
    return [
        (m.group(2), m.group(3) or "")
        for m in _MD_LINK_RE.finditer(content_text(text))
    ]


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract markdown doc nodes with authority tagging.

    When ``source["extract_sections"]`` is truthy, also emits section-level
    nodes for each H2 heading found in the document, linked to the parent
    doc node via ``contains`` edges.

    Inter-doc ``[label](other.md)`` references are extracted in a second
    pass and emitted as ``relates_to`` edges between the source ``doc:*``
    node and the resolved target ``doc:*`` node. Edges are only emitted
    when the resolved target file would itself produce a doc node by this
    same strategy invocation (i.e. the target is in the same glob set and
    not excluded). Anchors are stripped before resolution; missing targets,
    self-links, and non-``.md`` links are silently skipped.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    id_prefix = source.get("id_prefix", "doc")
    doc_kind = source.get("doc_kind") or _infer_doc_kind(id_prefix)
    authority = "canonical" if doc_kind in _CANONICAL_KINDS else "derived"
    do_sections = bool(source.get("extract_sections", False))
    # ``include_readme`` opts the default README.md skip out so a source
    # entry can deliberately index project READMEs as doc nodes (closes
    # the 2026-05-15 dogfood gap where root README and ``weld/README.md``
    # were absent from the graph entirely). Default False preserves the
    # historical behaviour for ``docs/*.md`` and ``docs/adrs/*.md``.
    include_readme = bool(source.get("include_readme", False))

    # Pass 1: collect (path, nid) pairs and a resolved-path -> nid map so
    # the link-extraction pass can verify each target would itself
    # produce a node before emitting an edge.
    #
    # Provenance is recorded per file in the emission pass below, never from
    # the glob's parent directory -- this glob is often root-anchored
    # (``README.md``), and the parent-derived entry was then ``"./"``, the
    # marker that makes every path in the repository count as tracked source
    # (bd 8ia5).
    md_paths: list[Path] = []
    path_to_nid: dict[Path, str] = {}
    for md in resolve_glob(root, pattern, excludes):
        if md.name == "README.md" and not include_readme:
            continue
        nid = f"{id_prefix}/{md.stem}"
        md_paths.append(md)
        try:
            path_to_nid[md.resolve()] = nid
        except OSError:
            path_to_nid[md] = nid

    for md in md_paths:
        rel_path = rel_to_root(md, root)
        discovered_from.append(rel_path)
        nid = path_to_nid.get(md.resolve(), f"{id_prefix}/{md.stem}")

        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            text = None

        label = _doc_label(md, text, id_prefix)

        doc_props: dict = {
            "file": rel_path,
            "doc_kind": doc_kind,
            "source_strategy": "markdown",
            "authority": authority,
            "confidence": "definite",
            "roles": ["doc"],
        }
        # ``props.headings`` lifts H2/H3 heading text out of the file body
        # and onto the doc node so the inverted index and runtime match
        # surface can tokenize them. Closes the 2026-05-15 ``wd query
        # 'language support'`` gap. Empty headings list is dropped so we
        # never index empty-string tokens.
        if text is not None:
            headings = _collect_heading_texts(text)
            if headings:
                doc_props["headings"] = headings
        nodes[nid] = {"type": "doc", "label": label, "props": doc_props}

        if text is None:
            continue

        # -- Inter-doc link extraction --
        link_targets: set[str] = set()
        for href, _anchor in _extract_md_link_targets(text):
            try:
                target_path = (md.parent / href).resolve()
            except OSError:
                continue
            target_nid = path_to_nid.get(target_path)
            if target_nid is None or target_nid == nid:
                # Unknown target (missing file, excluded, outside glob)
                # or self-link -- skip silently.
                continue
            link_targets.add(target_nid)
        # -- Doc -> code citation extraction (ADR 0128, bd ziv1) --
        edges.extend(code_reference_edges(root, nid, rel_path, text))

        for target_nid in sorted(link_targets):
            edges.append({
                "from": nid,
                "to": target_nid,
                "type": "relates_to",
                "props": {
                    "source_strategy": "markdown",
                    "authority": "derived",
                    "confidence": "inferred",
                    # ADR 0074: the file whose scan produced this edge --
                    # the *mentioning* markdown file, never the target it
                    # resolved. See the module docstring for why this is
                    # stamped even though the edge is not at risk today.
                    # ``rel_path`` verbatim, not a re-normalised copy: the
                    # purge tests this against a stale set built by
                    # ``_source_resolve``, and it is also the node's own
                    # ``props.file``. Since bd v552 / ADR 0112 both sides
                    # are constructed by ``weld._rel_path.rel_to_root``, so
                    # they agree by construction on every platform rather
                    # than only where ``str()`` and ``as_posix()`` coincide
                    # -- re-spelling it here could only reintroduce a
                    # divergence that retains edges whose producing file
                    # *is* stale.
                    "provenance": {"file": rel_path},
                },
            })

        # -- Section-level extraction (opt-in) --
        if do_sections:
            sections = _parse_sections(text)
            for sec in sections:
                sec_nid = f"{nid}#{sec['slug']}"
                sec_props: dict = {
                    "file": rel_path,
                    "doc_kind": doc_kind,
                    "source_strategy": "markdown",
                    "authority": authority,
                    "confidence": "inferred",
                    "roles": ["doc"],
                    "span": {
                        "start_line": sec["start_line"],
                        "end_line": sec["end_line"],
                    },
                }
                if sec["section_kind"] is not None:
                    sec_props["section_kind"] = sec["section_kind"]

                nodes[sec_nid] = {
                    "type": "doc",
                    "label": sec["heading"],
                    "props": sec_props,
                }
                edges.append({
                    "from": nid,
                    "to": sec_nid,
                    "type": "contains",
                    "props": {
                        "source_strategy": "markdown",
                        "confidence": "definite",
                    },
                })

    return StrategyResult(nodes, edges, discovered_from)
