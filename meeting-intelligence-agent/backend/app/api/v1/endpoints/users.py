"""User API Endpoints"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.user import User

router = APIRouter()


class UserResponse(BaseModel):
    id: UUID
    email: str
    username: str
    full_name: str
    role: Optional[str] = None
    timezone: Optional[str]
    department: Optional[str]
    job_title: Optional[str]
    preferences: Optional[dict]
    notification_settings: Optional[dict]

    class Config:
        from_attributes = True


class UserSettingsUpdate(BaseModel):
    full_name: Optional[str] = None
    timezone: Optional[str] = None
    department: Optional[str] = None
    job_title: Optional[str] = None
    preferences: Optional[dict] = None
    notification_settings: Optional[dict] = None


@router.get("/", response_model=list[UserResponse])
async def list_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List users (admin sees all, non-admin sees only self)"""
    if current_user.role != "admin":
        return [current_user]

    result = db.execute(select(User))
    return result.scalars().all()


@router.get("/me", response_model=UserResponse)
async def get_my_profile(
    current_user: User = Depends(get_current_user),
):
    """Get current user profile"""
    return current_user


@router.patch("/me", response_model=UserResponse)
async def update_my_profile(
    payload: UserSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update current user settings/profile"""
    update_data = payload.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(current_user, field, value)

    db.commit()
    db.refresh(current_user)
    return current_user
