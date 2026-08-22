"""Go enrichments for the shared tree-sitter strategy."""

from __future__ import annotations

from pathlib import Path

from weld.strategies._go_origin import (
    classify_go_import,
    parse_go_mod_module_path,
    strip_quotes,
)


def load_module_path(root: Path) -> str:
    """Return the module path declared by ``root/go.mod``, or ``""``."""
    try:
        text = (root / "go.mod").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return parse_go_mod_module_path(text)


def strip_import_quotes(imports: list[str]) -> list[str]:
    """Return *imports* with each entry's surrounding quotes removed.

    ``weld/languages/go.yaml``'s ``imports`` query captures Go's
    ``interpreted_string_literal`` import-path node, so the raw
    tree-sitter token still carries its source quotes (``'"fmt"'``, not
    ``'fmt'``). Every sibling Tier-1 language either has no quoting in
    its import syntax (Python, C#, Java, Rust) or strips it in its own
    enrichment path before the value reaches ``props.imports_from``
    (TypeScript, via ``_typescript_tree_sitter._strip_quotes``) --
    nothing did that for Go. Left unfixed, ``props.imports_from`` was
    the one language whose value did not match what every other
    consumer of that field already expects: a clean import path/module
    string. That silently defeated any exact-string matcher over it,
    including :mod:`weld.cross_repo.package_import_resolver` (bd bt5m).

    Order is preserved (callers may still want positional/original
    order even though :func:`import_origin_map` itself sorts).
    :func:`weld.strategies._go_origin.strip_quotes` already tolerates
    both quoted and unquoted input (idempotent), so calling it here and
    again inside :func:`weld.strategies._go_origin.classify_go_import`
    on the now-clean strings is a safe no-op the second time -- there is
    exactly one implementation of the stripping rule, reused twice.
    """
    return [strip_quotes(item) for item in imports]


def import_origin_map(imports: list[str], module_path: str) -> dict[str, str]:
    """Return a deterministic map from raw import path to ADR-0042 origin."""
    return {
        import_path: classify_go_import(import_path, module_path)
        for import_path in sorted({item for item in imports if item})
    }


def stamp_import_origins(
    node_props: dict,
    imports: list[str],
    module_path: str,
) -> None:
    """Attach ``props.imports_origin`` for Go file-node imports."""
    origins = import_origin_map(imports, module_path)
    if origins:
        node_props["imports_origin"] = origins
