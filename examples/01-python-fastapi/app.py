"""Minimal FastAPI application for cortex discovery demo."""

from __future__ import annotations

from fastapi import FastAPI

from models import Item, ItemCreate

app = FastAPI(title="Item Store", version="0.1.0")

# In-memory store for demonstration purposes.
_items: list[Item] = [
    Item(id=1, name="Widget", price=9.99),
    Item(id=2, name="Gadget", price=19.99),
]
_next_id: int = 3


@app.get("/health")
def health_check() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok"}


@app.get("/items")
def list_items() -> list[Item]:
    """Return all items in the store."""
    return _items


@app.post("/items", status_code=201)
def create_item(payload: ItemCreate) -> Item:
    """Create a new item and return it."""
    global _next_id
    item = Item(id=_next_id, **payload.model_dump())
    _next_id += 1
    _items.append(item)
    return item
