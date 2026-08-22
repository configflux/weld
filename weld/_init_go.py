"""Go package-anchor wiring for ``wd init`` (bd 1wcjp / ADR 0132).

Split out for the same reason ``weld/_init_csharp.py`` exists: keeping
``weld/init.py`` under its line cap without losing the wiring itself.
Unlike C#'s multi-flag detection (sln/csproj/aspnet/efcore/tests),
``go_package`` fires on exactly one condition -- Go was detected at all
-- so this module is a single one-line-bodied function, not a
detector-plus-entry-list pair.
"""

from __future__ import annotations

from weld._init_framework_sources import _source_entry


def go_package_source_entry() -> str:
    """Return the ``go_package`` YAML source entry (ADR 0132).

    Anchors every Go package directory so the shared tree-sitter pass's
    promoted symbols satisfy ADR 0041 Layer 3 file-anchor-symmetry, and
    so ``package_import_resolver`` has a genuine producer node to match
    a sibling repo's ``imports_from`` against.
    """
    return _source_entry(
        "**/*.go", "file", "go_package",
        comment="Go package anchors (ADR 0132)",
    )
