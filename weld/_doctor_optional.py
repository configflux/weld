"""Optional-dependency diagnostic checks for ``wd doctor``.

Factored out of ``weld/doctor.py`` to keep the main entry point under
the 400-line cap. Covers:

- tree-sitter grammars referenced by ``discover.yaml``.
- Optional Python packages that weld imports conditionally
  (``mcp`` SDK, ``anthropic``, ``openai``, ``ollama``).
- The standalone GitHub Copilot CLI binary (``copilot``) used by
  the ``copilot-cli`` enrichment provider.

Importable is not always the same as usable. The ``mcp`` SDK has a
version floor -- the stdio server drives an API that arrived in 2.0 --
so an installed 1.x SDK satisfies the import and satisfies nothing else.
Reporting it as present sent users to launch a server that refuses to
start; it is now reported as the degraded state it is, with the upgrade
that fixes it. The floor itself lives in :mod:`weld._mcp_sdk`, shared
with the server's own guard so the two cannot tell different stories.

Security posture: this module never prints filesystem paths or
environment variables. It reports import availability, the ``pip
install`` extra name for Python deps, the github-docs install URL for
``copilot-cli``, and -- for a dep that is installed but too old -- the
version of that dep as its own metadata already declares it.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from weld import _mcp_sdk
from weld._mcp_sdk import REQUIRED_SPEC, UPGRADE_COMMAND
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

    ``usability`` is an optional second stage for deps that can be
    installed and still be unusable. It runs only after ``check`` has
    passed, and returns ``None`` when the dep is fine or the clause
    describing the degradation -- remedy included -- when it is not. Only
    the ``mcp`` SDK needs it today: it has a version floor. Probes that
    leave it ``None`` keep the plain present/missing split.
    """

    display: str
    check: object  # Callable[[], bool], typed loosely for cheap closures.
    install_hint: str
    usability: object | None = None  # Callable[[], str | None]


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


def _mcp_usability() -> str | None:
    """Describe an installed ``mcp`` SDK the stdio server cannot drive.

    Returns ``None`` for an SDK weld can use. The wording tells the same
    story as the server's own refusal in :mod:`weld._mcp_stdio`: the SDK is
    installed, so the remedy is an upgrade -- never a reinstall of an extra
    the user demonstrably already has.

    Both readings -- the verdict and the version quoted back with it -- go
    through the :mod:`weld._mcp_sdk` module rather than through names copied
    into this namespace at import. One environment then has exactly one place
    that answers "which SDK is installed", so the two readings cannot come
    from different sources and disagree. They once could, and the line that
    produced said an SDK was installed *and* below a floor it plainly
    cleared.
    """
    if _mcp_sdk.sdk_usable():
        return None
    version = _mcp_sdk.installed_version() or "version unknown"
    return (
        f"installed ({version}) but the MCP stdio server requires "
        f"{REQUIRED_SPEC} -- {UPGRADE_COMMAND}"
    )


def _python_probe(
    module: str, display: str, extra: str, usability: object | None = None
) -> _Probe:
    return _Probe(
        display=display,
        check=lambda mod=module: _module_available(mod),
        install_hint=f"pip install 'configflux-weld[{extra}]'",
        usability=usability,
    )


def _build_probes() -> tuple[_Probe, ...]:
    return (
        _python_probe("mcp", "mcp SDK", "mcp", usability=_mcp_usability),
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

    A dep that is installed but unusable -- today only an ``mcp`` SDK
    below the stdio server's version floor -- belongs to neither summary
    and gets its own ``warn``. It is not "missing", because saying so
    would send someone who installed it to install it again; and it is
    not "present", because the capability it stands for does not work.
    ``warn`` rather than ``note`` follows this command's documented
    vocabulary: a currently-degraded state, non-fatal like every other
    Optional finding.
    """
    probes = _build_probes()

    present: list[str] = []
    missing: list[_Probe] = []
    degraded: list[tuple[_Probe, str]] = []
    for probe in probes:
        if not probe.check():
            missing.append(probe)
            continue
        # Second stage runs only on a dep that is actually installed, so an
        # absent dep can never be reported as one needing an upgrade.
        detail = probe.usability() if probe.usability is not None else None
        if detail is None:
            present.append(probe.display)
        else:
            degraded.append((probe, detail))

    results: list = []
    if present:
        results.append(
            result_cls(
                "ok",
                f"optional deps present: {', '.join(present)}",
                "Optional",
            )
        )
    for probe, detail in degraded:
        results.append(
            result_cls("warn", f"{probe.display} {detail}", "Optional")
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
    if not present and not missing and not degraded:
        results.append(
            result_cls("ok", "no optional deps configured", "Optional")
        )
    return results
