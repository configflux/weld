"""HTTP acceptance fixture: order routes (server side)."""
from fastapi import APIRouter

router = APIRouter(prefix="/orders", tags=["orders"])

@router.get("/")
def list_orders():
    return []

@router.post("/")
def create_order():
    return {"id": 1}
