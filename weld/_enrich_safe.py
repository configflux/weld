"""Pre-instantiation provider gating for ``wd enrich``.

Two responsibilities, both run before ``weld.providers.resolve_provider``
is allowed to instantiate a real provider:

1. Resolve the provider name from the explicit argument or the
   ``WELD_ENRICH_PROVIDER`` environment fallback, and reject the empty
   case with a stable user-facing message.
2. Refuse, under ``--safe``, any provider that performs outbound network
   or LLM calls.

Splitting these out of :mod:`weld.enrich` keeps the orchestration module
focused on the enrichment loop and gives the safe-mode contract a small,
reviewable home that mirrors ``weld._discover_strategies`` for ADR 0024.
All currently registered providers (``anthropic``, ``openai``,
``ollama``) are network-bound, so safe mode currently refuses every
known provider; a future deterministic provider would simply be omitted
from :data:`weld.providers.NETWORK_PROVIDERS` to be permitted under safe
mode.
"""

from __future__ import annotations

import os
import sys

from weld.providers import NETWORK_PROVIDERS


class SafeModeRefusedError(RuntimeError):
    """Raised when ``wd enrich --safe`` refuses an LLM/network provider.

    The CLI converts this into a non-zero exit. Library callers can catch
    it to distinguish a deliberate safe-mode refusal from a configuration
    error or a provider runtime failure.
    """


def resolve_provider_name(provider_name: str | None) -> str:
    """Return the provider name to use, defaulting to the env fallback.

    Raises :class:`ValueError` when no provider was supplied via either
    the explicit argument or ``WELD_ENRICH_PROVIDER``.
    """
    resolved = (provider_name or os.getenv("WELD_ENRICH_PROVIDER", "")).strip().lower()
    if not resolved:
        raise ValueError(
            "provider is required (use --provider or set WELD_ENRICH_PROVIDER)"
        )
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
