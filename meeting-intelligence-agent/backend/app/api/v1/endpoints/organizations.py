"""
Organization management endpoints
"""
from datetime import datetime, timedelta
import logging
import secrets
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import _serialize_user, _set_auth_cookies, require_admin
from app.core.database import get_db
from app.core.security import create_access_token, create_refresh_token, get_password_hash
from app.models.invite import Invite
from app.models.organization import Organization
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


class InviteRequest(BaseModel):
    email: EmailStr
    expires_in_days: int = Field(default=7, ge=1, le=30)


class InviteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    organization_id: UUID
    token: str
    status: str
    expires_at: datetime
    invite_link: str


class InvitePreviewResponse(BaseModel):
    email: EmailStr
    status: str
    expires_at: datetime
    organization: dict


class AcceptInviteRequest(BaseModel):
    token: str
    full_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = Field(default=None, min_length=8)


class AcceptInviteResponse(BaseModel):
    token_type: str = "bearer"
    invite_status: str
    organization: dict
    user: dict


def _invite_link(token: str) -> str:
    return f"http://localhost:3002/login?invite={token}"


@router.get("/invite-preview", response_model=InvitePreviewResponse)
async def invite_preview(
    token: str,
    db: Session = Depends(get_db),
):
    invite = db.execute(select(Invite).where(Invite.token == token)).scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")

    organization = db.execute(
        select(Organization).where(Organization.id == invite.organization_id)
    ).scalar_one_or_none()
    if not organization:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    return {
        "email": invite.email,
        "status": invite.status,
        "expires_at": invite.expires_at,
        "organization": {
            "id": str(organization.id),
            "name": organization.name,
            "slug": organization.slug,
        },
    }


@router.post("/invite", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def create_invite(
    payload: InviteRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    existing_member = db.execute(
        select(User).where(
            User.email == payload.email,
            User.organization_id == current_user.organization_id,
        )
    ).scalar_one_or_none()
    if existing_member:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is already a member of this organization")

    organization = db.execute(
        select(Organization).where(Organization.id == current_user.organization_id)
    ).scalar_one_or_none()
    if not organization:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(days=payload.expires_in_days)
    invite = db.execute(
        select(Invite).where(
            Invite.email == payload.email,
            Invite.organization_id == current_user.organization_id,
            Invite.status == "pending",
        )
    ).scalar_one_or_none()

    if invite:
        invite.token = token
        invite.expires_at = expires_at
    else:
        invite = Invite(
            email=payload.email,
            organization_id=current_user.organization_id,
            token=token,
            status="pending",
            expires_at=expires_at,
        )
        db.add(invite)

    db.commit()
    db.refresh(invite)

    invite_link = _invite_link(invite.token)
    logger.info(
        "Organization invite generated for %s (%s): %s",
        payload.email,
        organization.slug,
        invite_link,
    )

    return {
        "id": invite.id,
        "email": invite.email,
        "organization_id": invite.organization_id,
        "token": invite.token,
        "status": invite.status,
        "expires_at": invite.expires_at,
        "invite_link": invite_link,
    }


@router.post("/accept-invite", response_model=AcceptInviteResponse)
async def accept_invite(
    payload: AcceptInviteRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    invite = db.execute(select(Invite).where(Invite.token == payload.token)).scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.status != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite has already been used")
    if invite.expires_at < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite has expired")

    organization = db.execute(
        select(Organization).where(Organization.id == invite.organization_id)
    ).scalar_one_or_none()
    if not organization:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    user = db.execute(select(User).where(User.email == invite.email)).scalar_one_or_none()
    if user and str(user.organization_id) != str(invite.organization_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This email already belongs to another organization",
        )

    if not user:
        if not payload.full_name or not payload.username or not payload.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="full_name, username, and password are required to accept this invite",
            )

        existing_username = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()
        if existing_username:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already exists")

        user = User(
            organization_id=invite.organization_id,
            email=invite.email,
            username=payload.username,
            full_name=payload.full_name,
            hashed_password=get_password_hash(payload.password),
            role="member",
            is_active=True,
        )
        db.add(user)
        db.flush()

    invite.status = "accepted"
    db.commit()
    db.refresh(user)

    access_token = create_access_token(data={"sub": str(user.id), "org": str(user.organization_id), "role": user.role})
    refresh_token = create_refresh_token(data={"sub": str(user.id), "org": str(user.organization_id), "role": user.role})
    _set_auth_cookies(response, access_token, refresh_token)

    return {
        "invite_status": invite.status,
        "organization": {
            "id": str(organization.id),
            "name": organization.name,
            "slug": organization.slug,
        },
        "user": _serialize_user(user, organization),
    }
