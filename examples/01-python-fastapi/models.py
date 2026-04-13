"""Pydantic models for the item store API."""

from __future__ import annotations

from pydantic import BaseModel


class ItemCreate(BaseModel):
    """Payload for creating a new item."""

    name: str
    price: float


class Item(BaseModel):
    """An item in the store."""

    id: int
    name: str
    price: float
