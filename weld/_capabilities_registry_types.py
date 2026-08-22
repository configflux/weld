"""Capability *vocabulary* for the runtime matrix (ADR 0043 Layer B).

Split out of :mod:`weld._capabilities_registry`, which had reached the
400-line cap exactly, so a new strategy could not be registered without
breaking it. The split is along the seam the file already had: the shape
of a declaration lives here, the table of declarations stays there.
:mod:`weld._capabilities_registry` re-exports every name below, so
existing importers are unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Allowed evidence tags. Languages and frameworks share the same registry
# vocabulary; per-output filtering happens in
# :func:`weld.capabilities.compute_capabilities`.
LANGUAGE_EVIDENCE: frozenset[str] = frozenset(["file", "module", "imports", "symbols", "calls", "tests"])
FRAMEWORK_EVIDENCE: frozenset[str] = frozenset(["nodes_emitted", "srcs_edges", "deps_edges", "test_edges"])


@dataclass(frozen=True)
class StrategyCapability:
    """What a single strategy contributes to the capability matrix.

    A strategy is allowed to attribute to a language, a framework, or
    both. ``evidence`` lists the flags the strategy is *capable* of
    producing in principle; runtime crosses this with the actual graph
    contents in :func:`weld.capabilities.compute_capabilities`.

    Per ADR 0046 (multi-language test-peer edges), a strategy may
    attribute to *several* languages. The ``languages`` field is the
    multi-language form; ``language`` remains the single-language fast
    path. Consumers that need the full set should iterate
    :func:`languages_set` (handles the union deterministically). It is
    invalid to set both fields to non-empty values for the same entry.

    The same multi/single split applies to frameworks: the ``manifest``
    strategy processes both ``package.json`` (npm) and ``Makefile``
    (make), so it declares ``frameworks={'npm', 'make'}`` rather than a
    single ``framework='npm'`` (which would misclassify a Makefile-only
    repo). Consumers should iterate :func:`frameworks_set` to handle
    both shapes deterministically. It is invalid to set both
    ``framework`` and ``frameworks`` to non-empty values for the same
    entry.
    """

    language: str | None = None
    languages: frozenset[str] = field(default_factory=frozenset)
    framework: str | None = None
    frameworks: frozenset[str] = field(default_factory=frozenset)
    evidence: frozenset[str] = field(default_factory=frozenset)
    file_extensions: frozenset[str] = field(default_factory=frozenset)
    file_basenames: frozenset[str] = field(default_factory=frozenset)

    def languages_set(self) -> frozenset[str]:
        """Return all languages this capability attributes to.

        Returns the multi-language ``languages`` field when populated,
        otherwise wraps ``language`` in a singleton (or an empty set
        for framework-only entries).
        """
        if self.languages:
            return self.languages
        if self.language is not None:
            return frozenset((self.language,))
        return frozenset()

    def frameworks_set(self) -> frozenset[str]:
        """Return all frameworks this capability attributes to.

        Returns the multi-framework ``frameworks`` field when populated,
        otherwise wraps ``framework`` in a singleton (or an empty set
        for language-only entries).
        """
        if self.frameworks:
            return self.frameworks
        if self.framework is not None:
            return frozenset((self.framework,))
        return frozenset()


def _lang(
    name: str, evidence: tuple[str, ...], exts: tuple[str, ...] = (),
) -> StrategyCapability:
    return StrategyCapability(
        language=name,
        evidence=frozenset(evidence),
        file_extensions=frozenset(exts),
    )


def _multi_lang(
    names: tuple[str, ...],
    evidence: tuple[str, ...],
    exts: tuple[str, ...] = (),
) -> StrategyCapability:
    """Multi-language entry constructor.

    Used by ``test_peer`` (ADR 0046) so a single strategy can claim
    the ``tests`` evidence flag across Python, Go, TS/JS, Java, C#,
    and Rust without registering one stem per language.
    """
    return StrategyCapability(
        languages=frozenset(names),
        evidence=frozenset(evidence),
        file_extensions=frozenset(exts),
    )


def _fw(
    name: str,
    evidence: tuple[str, ...],
    exts: tuple[str, ...] = (),
    basenames: tuple[str, ...] = (),
) -> StrategyCapability:
    return StrategyCapability(
        framework=name,
        evidence=frozenset(evidence),
        file_extensions=frozenset(exts),
        file_basenames=frozenset(basenames),
    )


def _multi_fw(
    names: tuple[str, ...],
    evidence: tuple[str, ...],
    exts: tuple[str, ...] = (),
    basenames: tuple[str, ...] = (),
) -> StrategyCapability:
    """Multi-framework entry constructor.

    Used by ``manifest`` so a single strategy can claim both ``npm``
    (``package.json``) and ``make`` (``Makefile``/``GNUmakefile``)
    without splitting into two registry entries (which would break the
    one-stem-per-strategy registry-vs-disk invariant).
    """
    return StrategyCapability(
        frameworks=frozenset(names),
        evidence=frozenset(evidence),
        file_extensions=frozenset(exts),
        file_basenames=frozenset(basenames),
    )

