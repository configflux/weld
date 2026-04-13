"""Item Pydantic schemas."""

from pydantic import BaseModel

class ItemBase(BaseModel):
    title: str
    description: str | None = None
    price: float

class ItemCreate(ItemBase):
    pass

class ItemResponse(ItemBase):
    id: int
    owner_id: int

    class Config:
        from_attributes = True
