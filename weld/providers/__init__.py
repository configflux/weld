"""Provider interface and shared helpers for semantic enrichment."""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import Protocol

_COMPLEXITY_HINTS = frozenset(["low", "medium", "high"])
_PROVIDER_LOADERS = {
    "anthropic": ("weld.providers.anthropic", "AnthropicProvider"),
    "openai": ("weld.providers.openai", "OpenAIProvider"),
    "ollama": ("weld.providers.ollama", "OllamaProvider"),
    "copilot-cli": ("weld.providers.copilot_cli", "CopilotCliProvider"),
}
# Providers that perform outbound network or LLM calls. ``wd enrich --safe``
# refuses every name listed here. Currently every registered provider falls
# in this set: anthropic and openai hit hosted HTTPS APIs, ollama issues
# HTTP requests to a local or remote ollama server, and copilot-cli shells
# out to the GitHub Copilot CLI which talks to a hosted LLM. If a future
# deterministic provider is added, omit it from this set so safe mode
# permits it. See ADR 0024 for the discovery trust boundary; the enrich-side
# extension is tracked in the project issue ledger.
NETWORK_PROVIDERS = frozenset(_PROVIDER_LOADERS)


@dataclass(frozen=True)
class EnrichmentResult:
    """Validated semantic enrichment plus optional usage metadata."""

    description: str
    purpose: str | None = None
    complexity_hint: str | None = None
    suggested_tags: tuple[str, ...] = ()
    tokens_used: int = 0
    cost_usd: float = 0.0


class EnrichmentProvider(Protocol):
    """Protocol implemented by concrete enrichment providers."""

    DEFAULT_MODEL: str

    def enrich(self, node: dict, neighbors: list[dict], *, model: str) -> EnrichmentResult:
        """Return semantic enrichment for *node* grounded in *neighbors*."""


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _coerce_tags(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("provider output suggested_tags must be a list when present")
    tags: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("provider output suggested_tags must contain only strings")
        cleaned = item.strip().lower()
        if cleaned and cleaned not in tags:
            tags.append(cleaned)
    return tuple(tags)


def build_prompt(node: dict, neighbors: list[dict]) -> str:
    """Return the enrichment prompt for *node* and its 1-hop *neighbors*."""

    node_view = {
        "id": node.get("id"),
        "type": node.get("type"),
        "label": node.get("label"),
        "props": node.get("props") or {},
    }
    neighbor_view = [
        {
            "id": neighbor.get("id"),
            "type": neighbor.get("type"),
            "label": neighbor.get("label"),
            "props": neighbor.get("props") or {},
        }
        for neighbor in neighbors
    ]
    return (
        "You are enriching a knowledge-graph node for weld.\n"
        "Return JSON only with keys: description, purpose, complexity_hint, suggested_tags.\n"
        "Rules:\n"
        "- description is required and must be one concise sentence.\n"
        "- purpose is optional and should be one concise sentence.\n"
        "- complexity_hint must be one of low, medium, high when present.\n"
        "- suggested_tags must be a short list of lower-case strings.\n"
        "- Do not wrap the JSON in markdown fences.\n\n"
        f"Target node:\n{json.dumps(node_view, indent=2, ensure_ascii=True)}\n\n"
        f"1-hop neighbors:\n{json.dumps(neighbor_view, indent=2, ensure_ascii=True)}\n"
    )


def parse_json_result(
    text: str,
    *,
    tokens_used: int = 0,
    cost_usd: float = 0.0,
) -> EnrichmentResult:
    """Parse provider output into a validated :class:`EnrichmentResult`."""

    payload = json.loads(_strip_code_fences(text))
    if not isinstance(payload, dict):
        raise ValueError("provider output must be a JSON object")

    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("provider output missing non-empty description")

    purpose = payload.get("purpose")
    if purpose is not None:
        if not isinstance(purpose, str):
            raise ValueError("provider output purpose must be a string when present")
        purpose = purpose.strip() or None

    complexity_hint = payload.get("complexity_hint")
    if complexity_hint is not None:
        if not isinstance(complexity_hint, str):
            raise ValueError("provider output complexity_hint must be a string when present")
        complexity_hint = complexity_hint.strip().lower() or None
        if complexity_hint is not None and complexity_hint not in _COMPLEXITY_HINTS:
            raise ValueError("provider output complexity_hint must be one of low, medium, high")

    return EnrichmentResult(
        description=description.strip(),
        purpose=purpose,
        complexity_hint=complexity_hint,
        suggested_tags=_coerce_tags(payload.get("suggested_tags")),
        tokens_used=max(int(tokens_used or 0), 0),
        cost_usd=max(float(cost_usd or 0.0), 0.0),
    )


def build_edge_prompt(edge: dict, from_node: dict, to_node: dict) -> str:
    """Return the enrichment prompt for a cross-repo *edge* and its endpoint nodes."""

    edge_view = {
        "from": edge.get("from"),
        "to": edge.get("to"),
        "type": edge.get("type"),
        "props": edge.get("props") or {},
    }
    from_view = {
        "id": from_node.get("id"),
        "type": from_node.get("type"),
        "label": from_node.get("label"),
        "props": from_node.get("props") or {},
    }
    to_view = {
        "id": to_node.get("id"),
        "type": to_node.get("type"),
        "label": to_node.get("label"),
        "props": to_node.get("props") or {},
    }
    return (
        "You are enriching a cross-repo edge in a federated connected structure.\n"
        "Return JSON only with keys: description.\n"
        "Rules:\n"
        "- description is required and must be one concise sentence explaining\n"
        "  what this cross-repository relationship represents.\n"
        "- Do not wrap the JSON in markdown fences.\n\n"
        f"Target edge:\n{json.dumps(edge_view, indent=2, ensure_ascii=True)}\n\n"
        f"Source node (from):\n{json.dumps(from_view, indent=2, ensure_ascii=True)}\n\n"
        f"Target node (to):\n{json.dumps(to_view, indent=2, ensure_ascii=True)}\n"
    )


def resolve_provider(name: str) -> EnrichmentProvider:
    """Instantiate the provider named *name*."""

    normalized = name.strip().lower()
    if normalized not in _PROVIDER_LOADERS:
        valid = ", ".join(sorted(_PROVIDER_LOADERS))
        raise ValueError(f"unknown enrichment provider {name!r}; expected one of: {valid}")
    module_name, class_name = _PROVIDER_LOADERS[normalized]
    module = importlib.import_module(module_name)
    provider_cls = getattr(module, class_name)
    return provider_cls()
