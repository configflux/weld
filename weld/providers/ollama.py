"""Ollama-backed semantic enrichment provider."""

from __future__ import annotations

from weld.providers import build_prompt, parse_json_result

_INSTALL_HINT = 'Ollama enrichment requires `pip install -e "weld/[ollama]"`.'


def _lookup(value: object, key: str) -> object:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


class OllamaProvider:
    """Provider that calls the official Ollama Python library."""

    DEFAULT_MODEL = "gemma3"

    def __init__(self, chat_fn: object | None = None) -> None:
        if chat_fn is not None:
            self._chat = chat_fn
            return
        try:
            from ollama import chat
        except ImportError as exc:  # pragma: no cover - exercised via lazy import path
            raise RuntimeError(_INSTALL_HINT) from exc
        self._chat = chat

    def enrich(self, node: dict, neighbors: list[dict], *, model: str):
        response = self._chat(
            model=model,
            messages=[{"role": "user", "content": build_prompt(node, neighbors)}],
        )
        message = _lookup(response, "message")
        text = _lookup(message, "content")
        tokens_used = int(_lookup(response, "prompt_eval_count") or 0) + int(
            _lookup(response, "eval_count") or 0
        )
        return parse_json_result(
            text if isinstance(text, str) else "",
            tokens_used=tokens_used,
        )
