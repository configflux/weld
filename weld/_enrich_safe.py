"""Pre-instantiation provider gating for ``wd enrich``.

Two responsibilities, both run before ``weld.providers.resolve_provider``
is allowed to instantiate a real provider:

1. Resolve the provider name from the explicit argument or the
   ``WELD_ENRICH_PROVIDER`` environment fallback, and reject the empty
   case with a stable, copy-pastable user-facing message that lists the
   currently-detected providers and points at the agent-direct
   workflow when no provider is installed.
2. Refuse, under ``--safe``, any provider that performs outbound network
   or LLM calls.

Splitting these out of :mod:`weld.enrich` keeps the orchestration module
focused on the enrichment loop and gives the safe-mode contract a small,
reviewable home that mirrors ``weld._discover_strategies`` for ADR 0024.
All currently registered providers (``anthropic``, ``openai``,
``ollama``, ``copilot-cli``) are network-bound, so safe mode currently
refuses every known provider; a future deterministic provider would
simply be omitted from :data:`weld.providers.NETWORK_PROVIDERS` to be
permitted under safe mode.

The no-provider error reuses :mod:`weld._doctor_optional`'s probes so
``wd doctor`` and ``wd enrich`` agree on which providers are installed:
when the user copies a name out of the ``Available:`` line, it is the
same name ``wd doctor`` would have endorsed.
"""

from __future__ import annotations

import os
import sys

from weld.providers import NETWORK_PROVIDERS, _PROVIDER_LOADERS

# Documented agent-direct entry point. Surfaced in error messages when no
# provider is installed (or under --safe, where every registered provider
# is refused) so users without provider credentials know what to run.
_AGENT_DIRECT_HINT = (
    "Or run /enrich-weld in your agent harness (no provider needed)."
)


class SafeModeRefusedError(RuntimeError):
    """Raised when ``wd enrich --safe`` refuses an LLM/network provider.

    The CLI converts this into a non-zero exit. Library callers can catch
    it to distinguish a deliberate safe-mode refusal from a configuration
    error or a provider runtime failure.
    """


def _available_provider_names() -> tuple[str, ...]:
    """Return registered enrichment provider names that look installed.

    Reuses the ``wd doctor`` probes so the two surfaces stay in lock
    step. Filters down to providers actually registered in
    :data:`weld.providers._PROVIDER_LOADERS` -- ``wd doctor`` also probes
    the ``mcp`` SDK, which is unrelated to enrichment and must not appear
    in the ``Available:`` line.

    Probe failures are treated as "not installed". The function never
    raises; an unexpected exception during detection collapses to an
    empty tuple so the no-provider error can still render.
    """
    try:
        from weld._doctor_optional import _build_probes
    except Exception:  # noqa: BLE001 -- detection is best-effort
        return ()

    try:
        probes = _build_probes()
    except Exception:  # noqa: BLE001 -- detection is best-effort
        return ()

    detected: list[str] = []
    for probe in probes:
        name = probe.display
        if name not in _PROVIDER_LOADERS:
            continue
        try:
            present = bool(probe.check())
        except Exception:  # noqa: BLE001 -- treat failures as missing
            present = False
        if present:
            detected.append(name)
    return tuple(detected)


def _format_no_provider_error(*, safe: bool) -> str:
    """Build the human-facing message for the missing-provider case.

    Two shapes:

    * Default (``safe=False``): list detected providers, point at the
      agent-direct path, and mention ``--safe`` for users who want to
      refuse network providers.
    * Safe (``safe=True``): every registered provider is network-bound
      and therefore refused, so steer users at the agent-direct path
      instead of recommending ``pip install`` (which would defeat
      safe mode).

    ``WELD_ENRICH_PROVIDER`` is preserved in both shapes so existing
    documentation and grep-friendly support recipes still work.
    """
    if safe:
        return (
            "--provider required (use --provider or set "
            "WELD_ENRICH_PROVIDER); --safe refuses every registered "
            "(network) provider, so enrichment in safe mode requires "
            f"the agent-direct workflow. {_AGENT_DIRECT_HINT}"
        )

    available = _available_provider_names()
    lines = ["--provider required (use --provider or set WELD_ENRICH_PROVIDER)."]
    if available:
        lines.append(
            "Available: "
            + ", ".join(available)
            + " (install extras with `pip install configflux-weld[<name>]`)."
        )
    lines.append(_AGENT_DIRECT_HINT)
    lines.append("Pass --safe to refuse network providers.")
    return " ".join(lines)


def resolve_provider_name(
    provider_name: str | None, *, safe: bool = False
) -> str:
    """Return the provider name to use, defaulting to the env fallback.

    Raises :class:`ValueError` when no provider was supplied via either
    the explicit argument or ``WELD_ENRICH_PROVIDER``. When ``safe`` is
    true, the error specifically acknowledges the safe-mode + no-provider
    terminal state and points at the agent-direct workflow.
    """
    resolved = (provider_name or os.getenv("WELD_ENRICH_PROVIDER", "")).strip().lower()
    if not resolved:
        raise ValueError(_format_no_provider_error(safe=safe))
    return resolved


def refuse_if_network_provider(provider_name: str, *, safe: bool) -> None:
    """Refuse a network/LLM provider when ``safe`` is True.

    No-op when ``safe`` is False or when the provider name is not
    registered as network-bound. When refusing, writes a stable,
    grep-friendly line to stderr and raises :class:`SafeModeRefusedError`
    so callers can short-circuit before instantiating the provider or
    mutating the graph.
    """
    if not safe:
        return
    if provider_name in NETWORK_PROVIDERS:
        sys.stderr.write(
            f"[weld] safe mode: refused enrichment provider '{provider_name}'\n"
        )
        raise SafeModeRefusedError(
            f"safe mode refused enrichment provider {provider_name!r}: "
            "this provider performs network/LLM calls"
        )
