"""Item API endpoints."""

from fastapi import APIRouter, Depends

from app.schemas.item import ItemCreate, ItemResponse
from app.services.item_service import ItemService

router = APIRouter()

@router.get("/", response_model=list[ItemResponse])
def list_items(service: ItemService = Depends()):
    return service.list_all()

@router.post("/", response_model=ItemResponse, status_code=201)
def create_item(body: ItemCreate, service: ItemService = Depends()):
    return service.create(body)
