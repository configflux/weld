"""Rust enrichments for the shared tree-sitter strategy."""

from __future__ import annotations

from pathlib import Path

from weld.strategies._rust_origin import (
    classify_rust_use_path,
    parse_cargo_dependencies,
    parse_cargo_package_name,
)

#: A pair ``(package_name, dependencies)`` materialised once per
#: discovery run. The first element is the Rust-import shape of
#: ``[package].name`` (empty when no manifest is parsed); the second
#: is the union of declared dependency-table keys.
CargoMetadata = tuple[str, frozenset[str]]

EMPTY_CARGO_METADATA: CargoMetadata = ("", frozenset())


def load_cargo_metadata(root: Path, language: str) -> CargoMetadata:
    """Return ``(package_name, dependencies)`` from ``root/Cargo.toml``.

    Only inspects the manifest when *language* is ``"rust"``; for any
    other language the helper short-circuits to
    :data:`EMPTY_CARGO_METADATA` so the caller can use one assignment
    line regardless of language.

    On any error (missing file, decode failure, malformed TOML) the
    helper returns :data:`EMPTY_CARGO_METADATA` so callers can keep
    going against a graph that simply has no manifest evidence.
    """
    if language != "rust":
        return EMPTY_CARGO_METADATA
    try:
        text = (root / "Cargo.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return EMPTY_CARGO_METADATA
    return parse_cargo_package_name(text), parse_cargo_dependencies(text)


def import_origin_map(
    imports: list[str], cargo: CargoMetadata,
) -> dict[str, str]:
    """Return a deterministic map from raw use-path to ADR-0042 origin."""
    package_name, dependencies = cargo
    return {
        use_path: classify_rust_use_path(
            use_path,
            package_name=package_name,
            dependencies=dependencies,
        )
        for use_path in sorted({item for item in imports if item})
    }


def stamp_import_origins(
    node_props: dict, imports: list[str], cargo: CargoMetadata,
) -> None:
    """Attach ``props.imports_origin`` for Rust file-node imports."""
    origins = import_origin_map(imports, cargo)
    if origins:
        node_props["imports_origin"] = origins
