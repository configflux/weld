"""Project-local strategy capability manifests (ADR 0087).

Bundled strategies declare capabilities in the static
:data:`weld._capabilities_registry.STRATEGY_CAPABILITIES` table. Project-local
strategies (``.weld/strategies/*.py``, ADR 0024) cannot edit that in-tree
table, so they declare capabilities as **declarative data** instead -- read
here without importing or executing any project-local code. That keeps the
capability path safe-mode-permissible: under ``wd discover --safe`` weld still
refuses to *run* a project-local ``extract`` (ADR 0024), yet MAY read and
surface a declared capability, because reading a manifest executes no code.

Two declaration sites, both pure YAML data:

1. a ``capabilities:`` block on the strategy's ``.weld/discover.yaml`` source
   entry, or
2. a sibling ``.weld/strategies/<stem>.yaml`` manifest (optionally wrapped in a
   top-level ``capabilities:`` key).

The inline ``capabilities:`` block wins when both are present. Fields mirror
:class:`weld._capabilities_registry.StrategyCapability`: ``language`` /
``languages``, ``framework`` / ``frameworks``, ``evidence``,
``file_extensions``, ``file_basenames``.

The caller (:mod:`weld.capabilities`) merges these into the matrix with
**bundled-registry-wins** precedence and still applies ADR 0043's evidence
rule, so a declared capability never flips a flag true without matching graph
files -- a false declaration cannot spoof real-looking support.
"""

from __future__ import annotations

import re
from pathlib import Path

from weld._capabilities_registry import StrategyCapability

# A project-local strategy stem must be a plain module identifier. This bounds
# the sibling-manifest path (``.weld/strategies/<stem>.yaml``) to the
# strategies directory: a crafted ``strategy:`` name such as
# ``../../etc/passwd`` fails the match and is never turned into a filesystem
# path, so a malicious manifest cannot traverse out of the repo.
_SAFE_STEM = re.compile(r"^[A-Za-z0-9_]+$")


def _as_str_set(value: object) -> frozenset[str]:
    """Coerce a scalar or list of scalars into a frozenset of non-empty strs."""
    if isinstance(value, str):
        return frozenset((value,)) if value else frozenset()
    if isinstance(value, (list, tuple)):
        return frozenset(
            str(v) for v in value if isinstance(v, (str, int, float)) and str(v)
        )
    return frozenset()


def capability_from_manifest(data: object) -> StrategyCapability | None:
    """Build a :class:`StrategyCapability` from a manifest mapping, or ``None``.

    Returns ``None`` when *data* is not a mapping, declares nothing usable
    (no language, framework, or evidence), or carries no file signature
    (``file_extensions`` / ``file_basenames``). ``languages`` / ``frameworks``
    (multi form) win over the singular ``language`` / ``framework`` so a
    manifest that sets both stays well-defined and never violates the
    mutual-exclusion invariant.

    **Trust boundary (ADR 0087 point 4).** A project-local declaration is
    honored only when it carries a file signature the evidence rule can match.
    A signature-less capability would hit ``_strategy_has_evidence_in_graph``'s
    structural-strategy path, which returns ``True`` unconditionally -- a
    concession trusted only for *bundled* strategies. For an untrusted
    project-local manifest that would flip a flag true on an empty graph, the
    exact spoof the evidence rule must prevent. Such declarations are dropped.
    """
    if not isinstance(data, dict):
        return None

    languages = _as_str_set(data.get("languages"))
    language = data.get("language")
    if languages or not isinstance(language, str) or not language:
        language = None

    frameworks = _as_str_set(data.get("frameworks"))
    framework = data.get("framework")
    if frameworks or not isinstance(framework, str) or not framework:
        framework = None

    evidence = _as_str_set(data.get("evidence"))
    if (
        language is None
        and not languages
        and framework is None
        and not frameworks
        and not evidence
    ):
        return None

    file_extensions = _as_str_set(data.get("file_extensions"))
    file_basenames = _as_str_set(data.get("file_basenames"))
    if not file_extensions and not file_basenames:
        # No signature -> the evidence rule cannot gate this claim, so an
        # empty graph would still report support. Drop it (ADR 0087 point 4).
        return None

    return StrategyCapability(
        language=language,
        languages=languages,
        framework=framework,
        frameworks=frameworks,
        evidence=evidence,
        file_extensions=file_extensions,
        file_basenames=file_basenames,
    )


def _parse_yaml_file(path: Path) -> object:
    """Best-effort parse of a YAML file; ``None`` on any read/parse failure.

    Uses the bundled :func:`weld._yaml.parse_yaml` reader -- the same
    data-only path :mod:`weld.capabilities` already uses for ``discover.yaml``.
    No project code is imported or executed.
    """
    try:
        from weld._yaml import parse_yaml
    except Exception:
        return None
    try:
        return parse_yaml(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_sources(
    repo_root: Path,
) -> tuple[set[str], dict[str, StrategyCapability]]:
    """Return ``(wired_stems, inline_caps)`` from ``.weld/discover.yaml``.

    ``wired_stems`` are the ``strategy:`` names referenced by source entries
    (used to scope sibling-manifest reads); ``inline_caps`` maps a stem to the
    :class:`StrategyCapability` parsed from its ``capabilities:`` block, if any.
    Tolerates a missing or malformed file by returning empties.
    """
    cfg = repo_root / ".weld" / "discover.yaml"
    if not cfg.is_file():
        return set(), {}
    data = _parse_yaml_file(cfg)
    sources = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(sources, list):
        return set(), {}

    wired: set[str] = set()
    inline: dict[str, StrategyCapability] = {}
    for entry in sources:
        if not isinstance(entry, dict):
            continue
        name = entry.get("strategy")
        if not isinstance(name, str) or not _SAFE_STEM.match(name):
            continue
        wired.add(name)
        cap = capability_from_manifest(entry.get("capabilities"))
        if cap is not None:
            inline.setdefault(name, cap)
    return wired, inline


def _sibling_capabilities(
    repo_root: Path, wired: set[str],
) -> dict[str, StrategyCapability]:
    """Capabilities declared via sibling ``.weld/strategies/<stem>.yaml`` files.

    Scoped to *wired* stems so a stray manifest for an unwired strategy never
    leaks into the matrix. A top-level ``capabilities:`` wrapper is honored;
    otherwise the whole document is read as the capability mapping.
    """
    sdir = repo_root / ".weld" / "strategies"
    if not sdir.is_dir():
        return {}
    out: dict[str, StrategyCapability] = {}
    for stem in wired:
        if not _SAFE_STEM.match(stem):
            continue
        manifest = sdir / f"{stem}.yaml"
        if not manifest.is_file():
            continue
        data = _parse_yaml_file(manifest)
        if isinstance(data, dict) and "capabilities" in data:
            data = data.get("capabilities")
        cap = capability_from_manifest(data)
        if cap is not None:
            out[stem] = cap
    return out


def load_local_capabilities(repo_root: Path) -> dict[str, StrategyCapability]:
    """Return ``{stem: StrategyCapability}`` declared by project-local manifests.

    Reads only declarative YAML -- never imports a project-local strategy -- so
    it is safe-mode-permissible (ADR 0087). Combines two declaration sites:
    inline ``capabilities:`` blocks on ``discover.yaml`` source entries (which
    win) and sibling ``.weld/strategies/<stem>.yaml`` manifests, the latter
    scoped to stems actually wired in ``discover.yaml``. Best-effort: any parse
    failure yields an empty mapping so a malformed manifest never breaks the
    capability matrix.
    """
    try:
        wired, inline = _read_sources(repo_root)
        merged: dict[str, StrategyCapability] = _sibling_capabilities(
            repo_root, wired,
        )
        merged.update(inline)  # inline capabilities: block wins over sibling
        return merged
    except Exception:
        return {}
