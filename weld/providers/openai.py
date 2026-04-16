"""OpenAI-backed semantic enrichment provider."""

from __future__ import annotations

from weld.providers import build_prompt, parse_json_result

_INSTALL_HINT = 'OpenAI enrichment requires `pip install -e "weld/[openai]"`.'


class OpenAIProvider:
    """Provider that calls the official OpenAI Python SDK."""

    DEFAULT_MODEL = "gpt-5.4-mini"

    def __init__(self, client: object | None = None) -> None:
        if client is not None:
            self._client = client
            return
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - exercised via lazy import path
            raise RuntimeError(_INSTALL_HINT) from exc
        self._client = OpenAI()

    def enrich(self, node: dict, neighbors: list[dict], *, model: str):
        response = self._client.responses.create(
            model=model,
            input=build_prompt(node, neighbors),
        )
        usage = getattr(response, "usage", None)
        tokens_used = int(getattr(usage, "total_tokens", 0) or 0)
        return parse_json_result(
            getattr(response, "output_text", ""),
            tokens_used=tokens_used,
        )
