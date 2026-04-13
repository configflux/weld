from fastapi import APIRouter

from app.api.v1.users import router as users_router
from app.api.v1.items import router as items_router

router = APIRouter()
router.include_router(users_router, prefix="/users", tags=["users"])
router.include_router(items_router, prefix="/items", tags=["items"])
