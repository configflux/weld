from fastapi import APIRouter

router = APIRouter(prefix="/v1")

@router.get("/items")
def list_items():
    return []
