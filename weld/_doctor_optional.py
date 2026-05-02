"""Optional-dependency diagnostic checks for ``wd doctor``.

Factored out of ``weld/doctor.py`` to keep the main entry point under
the 400-line cap. Covers:

- tree-sitter grammars referenced by ``discover.yaml``.
- Optional Python packages that weld imports conditionally
  (``mcp`` SDK, ``anthropic``, ``openai``, ``ollama``).
- The standalone GitHub Copilot CLI binary (``copilot``) used by
  the ``copilot-cli`` enrichment provider.

Security posture: this module never prints filesystem paths or
environment variables. It only reports import availability, the
``pip install`` extra name for Python deps, or the github-docs install
URL for ``copilot-cli``.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from weld._yaml import parse_yaml
from weld.strategies._ts_parse import grammar_module_name, grammar_package_name


_TREE_SITTER_LANGUAGES = (
    "python", "javascript", "typescript", "go", "rust", "cpp", "csharp",
)


# Stable note ids for missing-optional-dep findings. Keep in sync with
# ``weld._doctor_suppressions.VALID_NOTE_IDS``.
_NOTE_ID_BY_DISPLAY: dict[str, str] = {
    "mcp SDK": "optional-mcp-missing",
    "anthropic": "optional-anthropic-missing",
    "openai": "optional-openai-missing",
    "ollama": "optional-ollama-missing",
    "copilot-cli": "optional-copilot-cli-missing",
}


_COPILOT_BINARY_ENV = "WELD_COPILOT_BINARY"
_COPILOT_DEFAULT_BINARY = "copilot"
_COPILOT_INSTALL_HINT = (
    "Install from https://docs.github.com/en/copilot/how-tos/use-copilot-cli "
    "or set WELD_COPILOT_BINARY to its absolute path"
)


@dataclass(frozen=True)
class _Probe:
    """One optional-dependency probe.

    ``check`` returns ``True`` when the dep is available. ``install_hint``
    is the human-readable installation instruction shown when the dep is
    missing -- a ``pip install`` line for Python deps, a github-docs URL
    for the ``copilot-cli`` binary.
    """

    display: str
    check: object  # Callable[[], bool], typed loosely for cheap closures.
    install_hint: str


def _module_available(mod_name: str) -> bool:
    try:
        return importlib.util.find_spec(mod_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _copilot_available() -> bool:
    """Mirror :class:`weld.providers.copilot_cli.CopilotCliProvider`'s resolution.

    Honours ``WELD_COPILOT_BINARY`` like the provider does, then falls
    back to the default ``copilot`` binary on ``PATH``.
    """
    name = os.getenv(_COPILOT_BINARY_ENV) or _COPILOT_DEFAULT_BINARY
    return shutil.which(name) is not None


def _python_probe(module: str, display: str, extra: str) -> _Probe:
    return _Probe(
        display=display,
        check=lambda mod=module: _module_available(mod),
        install_hint=f"pip install 'configflux-weld[{extra}]'",
    )


def _build_probes() -> tuple[_Probe, ...]:
    return (
        _python_probe("mcp", "mcp SDK", "mcp"),
        _python_probe("anthropic", "anthropic", "anthropic"),
        _python_probe("openai", "openai", "openai"),
        _python_probe("ollama", "ollama", "ollama"),
        _Probe(
            display="copilot-cli",
            check=_copilot_available,
            install_hint=_COPILOT_INSTALL_HINT,
        ),
    )


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
    """Summarise optional dependency availability.

    Emits a single ``ok`` summary of present deps, a single ``note``
    summary of missing deps, and one ``note`` per missing dep with its
    install hint. The ``copilot-cli`` probe walks the binary on ``PATH``
    (honouring ``WELD_COPILOT_BINARY``), so its hint is the github-docs
    URL rather than a ``pip install`` line.
    """
    probes = _build_probes()

    present: list[str] = []
    missing: list[_Probe] = []
    for probe in probes:
        if probe.check():
            present.append(probe.display)
        else:
            missing.append(probe)

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
        names = ", ".join(p.display for p in missing)
        # Summary line has no note_id: it is not individually suppressible
        # (acking each underlying entry will drop the summary by induction).
        results.append(
            result_cls(
                "note",
                f"optional deps missing: {names}",
                "Optional",
                note_id=None,
            )
        )
        for probe in missing:
            note_id = _NOTE_ID_BY_DISPLAY.get(probe.display)
            results.append(
                result_cls(
                    "note",
                    f"{probe.display} not installed -- {probe.install_hint}",
                    "Optional",
                    note_id=note_id,
                )
            )
    if not present and not missing:
        results.append(
            result_cls("ok", "no optional deps configured", "Optional")
        )
    return results
