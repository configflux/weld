"""User API endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.user import UserCreate, UserResponse
from app.services.user_service import UserService

router = APIRouter()

@router.get("/", response_model=list[UserResponse])
def list_users(service: UserService = Depends()):
    return service.list_all()

@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: int, service: UserService = Depends()):
    user = service.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.post("/", response_model=UserResponse, status_code=201)
def create_user(body: UserCreate, service: UserService = Depends()):
    return service.create(body)
