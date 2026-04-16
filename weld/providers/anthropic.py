"""Anthropic-backed semantic enrichment provider."""

from __future__ import annotations

from weld.providers import build_prompt, parse_json_result

_INSTALL_HINT = 'Anthropic enrichment requires `pip install -e "weld/[anthropic]"`.'
_MAX_OUTPUT_TOKENS = 400


class AnthropicProvider:
    """Provider that calls the official Anthropic Python SDK."""

    DEFAULT_MODEL = "claude-sonnet-4-5-20250929"

    def __init__(self, client: object | None = None) -> None:
        if client is not None:
            self._client = client
            return
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover - exercised via lazy import path
            raise RuntimeError(_INSTALL_HINT) from exc
        self._client = Anthropic()

    def enrich(self, node: dict, neighbors: list[dict], *, model: str):
        message = self._client.messages.create(
            model=model,
            max_tokens=_MAX_OUTPUT_TOKENS,
            messages=[{"role": "user", "content": build_prompt(node, neighbors)}],
        )
        text = "".join(
            getattr(block, "text", "")
            for block in (getattr(message, "content", None) or [])
            if getattr(block, "type", None) == "text"
        )
        usage = getattr(message, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        return parse_json_result(
            text,
            tokens_used=input_tokens + output_tokens,
        )
