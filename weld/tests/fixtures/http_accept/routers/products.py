"""HTTP acceptance fixture: product routes (server side)."""
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/products", tags=["products"])

@router.get("/")
def list_products():
    return []

@router.get("/{product_id}")
def get_product(product_id: int):
    return {"id": product_id}

@router.post("/")
def create_product():
    return {"id": 1}
