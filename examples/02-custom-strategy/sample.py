"""Sample module with TODO comments for the custom strategy demo."""

from __future__ import annotations


def process_data(records: list[dict]) -> list[dict]:
    """Process a list of records and return results."""
    results = []
    for record in records:
        # TODO: Add input validation for edge cases
        value = record.get("value", 0)
        results.append({"value": value * 2})
    return results


def load_config(path: str) -> dict:
    """Load configuration from a file path."""
    # FIXME: Handle missing config file gracefully
    with open(path, encoding="utf-8") as fh:
        import json
        return json.load(fh)


def summarize(records: list[dict]) -> dict:
    """Compute summary statistics over records."""
    if not records:
        return {"count": 0, "total": 0}
    total = sum(r.get("value", 0) for r in records)
    # TODO: Add median and standard deviation calculations
    return {"count": len(records), "total": total}
