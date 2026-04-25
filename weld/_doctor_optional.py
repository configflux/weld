"""Optional-dependency diagnostic checks for ``wd doctor``.

Factored out of ``weld/doctor.py`` to keep the main entry point under
the 400-line cap. Covers:

- tree-sitter grammars referenced by ``discover.yaml``.
- Optional Python packages that weld imports conditionally
  (``mcp`` SDK, ``anthropic``, ``openai``, ``ollama``).

Security posture: this module never prints filesystem paths or
environment variables. It only reports import availability and the
``pip install`` extra name.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from weld._yaml import parse_yaml
from weld.strategies._ts_parse import grammar_module_name, grammar_package_name


_TREE_SITTER_LANGUAGES = (
    "python", "javascript", "typescript", "go", "rust", "cpp", "csharp",
)


# Optional Python dependencies doctor probes. Each tuple is
# (module_to_import, display_name, extra_name). Keep this list conservative:
# only modules weld itself consumes.
_OPTIONAL_DEPENDENCIES: tuple[tuple[str, str, str], ...] = (
    ("mcp", "mcp SDK", "mcp"),
    ("anthropic", "anthropic", "anthropic"),
    ("openai", "openai", "openai"),
    ("ollama", "ollama", "ollama"),
)


def _module_available(mod_name: str) -> bool:
    try:
        return importlib.util.find_spec(mod_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _check_tree_sitter_language(lang: str) -> bool:
    mod_name = grammar_module_name(lang)
    try:
        spec = importlib.util.find_spec(mod_name)
        return spec is not None
    except (ModuleNotFoundError, ValueError):
        return False


def check_tree_sitter(weld_dir: Path, result_cls: type) -> list:
    """Check tree-sitter grammar availability for configured languages."""
    path = weld_dir / "discover.yaml"
    if not path.is_file():
        return []

    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
        sources = data.get("sources", []) if isinstance(data, dict) else []
    except Exception:
        return []

    uses_tree_sitter = any(
        isinstance(s, dict) and s.get("strategy") == "tree_sitter"
        for s in sources
        if isinstance(s, dict)
    )
    if not uses_tree_sitter:
        return []

    available: list[str] = []
    missing: list[str] = []
    for lang in _TREE_SITTER_LANGUAGES:
        if _check_tree_sitter_language(lang):
            available.append(lang)
        else:
            missing.append(lang)

    results: list = []
    if available:
        results.append(
            result_cls(
                "ok",
                f"tree-sitter available ({', '.join(available)})",
                "Optional",
            )
        )
    if missing:
        for lang in missing:
            display = "C#" if lang == "csharp" else lang.title()
            results.append(
                result_cls(
                    "warn",
                    f"{grammar_package_name(lang)} not installed -- "
                    f"{display} files using regex fallback",
                    "Optional",
                )
            )
    return results


def check_optional_deps(result_cls: type) -> list:
    """Summarise optional Python dependency availability.

    Emits a single ``ok`` summary of present deps, a single ``warn``
    summary of missing deps, and one ``warn`` per missing dep with its
    ``pip install 'configflux-weld[<extra>]'`` hint.
    """
    present: list[str] = []
    missing: list[tuple[str, str]] = []
    for mod_name, display, extra in _OPTIONAL_DEPENDENCIES:
        if _module_available(mod_name):
            present.append(display)
        else:
            missing.append((display, extra))

    results: list = []
    if present:
        results.append(
            result_cls(
                "ok",
                f"optional deps present: {', '.join(present)}",
                "Optional",
            )
        )
    if missing:
        names = ", ".join(d for d, _ in missing)
        results.append(
            result_cls(
                "warn",
                f"optional deps missing: {names}",
                "Optional",
            )
        )
        for display, extra in missing:
            results.append(
                result_cls(
                    "warn",
                    f"{display} not installed -- "
                    f"pip install 'configflux-weld[{extra}]'",
                    "Optional",
                )
            )
    if not present and not missing:
        results.append(
            result_cls("ok", "no optional deps configured", "Optional")
        )
    return results
