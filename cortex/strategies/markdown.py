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
"""

from __future__ import annotations

import re
from pathlib import Path

from cortex.strategies._helpers import StrategyResult, filter_glob_results, should_skip

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

# -- Section kind classification ---------------------------------------------
# Maps heading-text patterns (lowercased) to section_kind values.
# Order matters: first match wins.  Patterns are checked with substring
# matching against the lowercased heading text.
_SECTION_KIND_PATTERNS: list[tuple[str, str]] = [
    ("install", "setup"),
    ("setup", "setup"),
    ("getting started", "setup"),
    ("quickstart", "setup"),
    ("quick start", "setup"),
    ("prerequisite", "setup"),
    ("requirements", "setup"),
    ("config", "configuration"),
    ("environment variable", "configuration"),
    ("settings", "configuration"),
    ("api reference", "api-reference"),
    ("api doc", "api-reference"),
    ("endpoints", "api-reference"),
    ("architecture", "architecture"),
    ("design", "architecture"),
    ("system overview", "architecture"),
    ("component", "architecture"),
    ("troubleshoot", "troubleshooting"),
    ("debug", "troubleshooting"),
    ("common error", "troubleshooting"),
    ("common issue", "troubleshooting"),
    ("faq", "troubleshooting"),
    ("overview", "overview"),
    ("introduction", "overview"),
    ("summary", "overview"),
    ("context", "overview"),
    ("deploy", "deployment"),
    ("release", "deployment"),
    ("ci/cd", "deployment"),
    ("usage", "usage"),
    ("examples", "usage"),
    ("how to", "usage"),
    ("test", "testing"),
    ("verification", "testing"),
    ("migrat", "migration"),
    ("upgrade", "migration"),
    ("security", "security"),
    ("auth", "security"),
    ("permission", "security"),
    ("access control", "security"),
    ("contribut", "contributing"),
    ("development", "contributing"),
]

def _infer_doc_kind(id_prefix: str) -> str:
    """Infer doc_kind from the id_prefix when not explicitly configured."""
    for fragment, kind in _PREFIX_TO_KIND.items():
        if fragment in id_prefix:
            return kind
    return "guide"

def _classify_section(heading_text: str) -> str | None:
    """Classify a heading into a section_kind, or None if unrecognized.

    Only returns a classification when the heading text clearly matches
    a known pattern.  Returns None for ambiguous or generic headings to
    avoid overclaiming semantics.
    """
    lower = heading_text.lower()
    for pattern, kind in _SECTION_KIND_PATTERNS:
        if pattern in lower:
            return kind
    return None

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
    """
    lines = text.splitlines()
    sections: list[dict] = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            heading = stripped[3:].strip()
            sections.append({
                "heading": heading,
                "slug": _slugify(heading),
                "start_line": i + 1,  # 1-indexed
                "end_line": -1,  # filled in below
                "section_kind": _classify_section(heading),
            })

    # Fill in end_line for each section (up to the next section or EOF)
    for idx, sec in enumerate(sections):
        if idx + 1 < len(sections):
            sec["end_line"] = sections[idx + 1]["start_line"] - 1
        else:
            sec["end_line"] = len(lines)

    return sections

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract markdown doc nodes with authority tagging.

    When ``source["extract_sections"]`` is truthy, also emits section-level
    nodes for each H2 heading found in the document, linked to the parent
    doc node via ``contains`` edges.
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

    parent = (root / pattern).parent
    if not parent.is_dir():
        return StrategyResult(nodes, edges, discovered_from)
    discovered_from.append(str(parent.relative_to(root)) + "/")

    for md in filter_glob_results(root, sorted(parent.glob(Path(pattern).name))):
        if md.name == "README.md":
            continue
        if should_skip(md, excludes):
            continue
        rel_path = str(md.relative_to(root))
        nid = f"{id_prefix}/{md.stem}"
        if "runbook" in id_prefix:
            label = md.stem.replace("_", " ").title()
        else:
            label = md.stem.replace("-", " ").title()
        nodes[nid] = {
            "type": "doc",
            "label": label,
            "props": {
                "file": rel_path,
                "doc_kind": doc_kind,
                "source_strategy": "markdown",
                "authority": authority,
                "confidence": "definite",
                "roles": ["doc"],
            },
        }

        # -- Section-level extraction (opt-in) --
        if do_sections:
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                continue

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
