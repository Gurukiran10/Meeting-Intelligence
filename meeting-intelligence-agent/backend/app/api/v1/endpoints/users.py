"""User API Endpoints"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import require_org_member, require_admin
from app.core.database import get_db
from app.models.organization import Organization
from app.models.user import User

router = APIRouter()


def _serialize_user(user: User, organization: Optional[Organization]) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "full_name": user.full_name,
        "role": user.role,
        "timezone": user.timezone,
        "department": user.department,
        "job_title": user.job_title,
        "preferences": user.preferences,
        "notification_settings": user.notification_settings,
        "organization": (
            {
                "id": organization.id,
                "name": organization.name,
                "slug": organization.slug,
            }
            if organization
            else None
        ),
    }


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
    organization: Optional[dict] = None

    model_config = ConfigDict(from_attributes=True)


class UserSettingsUpdate(BaseModel):
    full_name: Optional[str] = None
    timezone: Optional[str] = None
    department: Optional[str] = None
    job_title: Optional[str] = None
    preferences: Optional[dict] = None
    notification_settings: Optional[dict] = None


@router.get("/", response_model=list[UserResponse])
async def list_users(
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """List users in the current organization workspace."""
    org = db.execute(select(Organization).where(Organization.id == current_user.organization_id)).scalar_one_or_none()
    result = db.execute(select(User).where(User.organization_id == current_user.organization_id))
    users = result.scalars().all()
    return [_serialize_user(user, org) for user in users]


@router.get("/me", response_model=UserResponse)
async def get_my_profile(
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Get current user profile"""
    org = db.execute(select(Organization).where(Organization.id == current_user.organization_id)).scalar_one_or_none()
    return _serialize_user(current_user, org)


@router.patch("/me", response_model=UserResponse)
async def update_my_profile(
    payload: UserSettingsUpdate,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Update current user settings/profile"""
    update_data = payload.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(current_user, field, value)

    db.commit()
    db.refresh(current_user)
    org = db.execute(select(Organization).where(Organization.id == current_user.organization_id)).scalar_one_or_none()
    return _serialize_user(current_user, org)
