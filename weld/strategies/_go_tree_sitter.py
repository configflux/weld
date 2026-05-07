"""Go enrichments for the shared tree-sitter strategy."""

from __future__ import annotations

from pathlib import Path

from weld.strategies._go_origin import (
    classify_go_import,
    parse_go_mod_module_path,
)


def load_module_path(root: Path) -> str:
    """Return the module path declared by ``root/go.mod``, or ``""``."""
    try:
        text = (root / "go.mod").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return parse_go_mod_module_path(text)


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
