"""Heading-text to ``section_kind`` classification for the markdown strategy.

A pure lookup table plus the substring match that reads it, extracted from
:mod:`weld.strategies.markdown` (bd m936). That module sat at 398 of the 400
line cap, so the next person to touch it would have taken a lint failure on
their own change and reached for the cheapest local fix -- deleting a comment
-- rather than the right one. This is the largest cohesive block in the file
with a single caller, no external references, and no dependency on anything
else in the strategy, which makes it the split with the biggest win and the
smallest blast radius.

**The table is ordered, and the order is the contract.** First match wins on
a substring test against the lowercased heading, so a heading reading
"Installation and Configuration" classifies as ``setup`` because ``install``
appears above ``config``. Entries are grouped by the kind they produce and
the groups are sequenced deliberately -- setup before configuration, the
narrow ``api reference`` before the broad ``architecture`` family -- so
appending a new pattern to the bottom is safe while inserting one mid-table
can silently re-classify existing docs.

**Unrecognized is a real answer, not a fallback.** :func:`classify_section`
returns ``None`` rather than guessing, because a section node carrying a
wrong ``section_kind`` is worse for a reader than one carrying none: a query
filtered by kind silently excludes the right section, whereas an unclassified
section still matches on its heading text.
"""

from __future__ import annotations

# Maps heading-text patterns (lowercased) to section_kind values.
# Order matters: first match wins. Patterns are checked with substring
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


def classify_section(heading_text: str) -> str | None:
    """Classify a heading into a section_kind, or None if unrecognized.

    Only returns a classification when the heading text clearly matches
    a known pattern. Returns None for ambiguous or generic headings to
    avoid overclaiming semantics.
    """
    lower = heading_text.lower()
    for pattern, kind in _SECTION_KIND_PATTERNS:
        if pattern in lower:
            return kind
    return None


__all__ = ["classify_section"]
