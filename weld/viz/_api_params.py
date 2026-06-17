"""Small param-parsing helpers extracted from :mod:`weld.viz.api`.

Lifted here so ``api.py`` can grow the summary payload without busting
the 400-line cap; these helpers are stateless string/int/scope coercions
that pair naturally and aren't worth their own homes.
"""

from __future__ import annotations

from typing import Any

from weld.viz.adapter import normalize_graph_data


def csv_set(raw: object) -> set[str] | None:
    if raw in (None, ""):
        return None
    values = [part.strip() for part in str(raw).split(",")]
    return {value for value in values if value} or None


def clean(raw: object) -> str:
    return str(raw).strip() if raw not in (None, "") else ""


def to_int(raw: object, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def required(params: dict[str, Any], key: str) -> str:
    value = clean(params.get(key))
    if not value:
        raise ValueError(f"{key} is required")
    return value


def is_child_scope(scope: str) -> bool:
    return scope.startswith("child:")


def child_name(scope: str) -> str:
    return scope.split(":", 1)[1]


def error_payload(message: str) -> dict:
    payload = normalize_graph_data({"nodes": {}, "edges": []})
    payload["warnings"] = [message]
    return payload
