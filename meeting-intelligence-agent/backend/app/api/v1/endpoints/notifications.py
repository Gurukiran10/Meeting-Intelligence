"""
In-app notification endpoints
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import require_org_member
from app.core.database import get_db
from app.models.notification import Notification
from app.models.user import User

router = APIRouter()


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    organization_id: UUID
    type: str
    message: str
    is_read: bool
    created_at: datetime
    notification_metadata: Optional[dict] = None


@router.get("/", response_model=List[NotificationResponse])
async def list_notifications(
    unread_only: bool = False,
    limit: int = 20,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    query = select(Notification).where(
        Notification.organization_id == current_user.organization_id,
        Notification.user_id == current_user.id,
    )
    if unread_only:
        query = query.where(Notification.is_read.is_(False))

    query = query.order_by(desc(Notification.created_at)).limit(limit)
    result = db.execute(query)
    return result.scalars().all()


@router.patch("/read-all", response_model=dict)
async def mark_all_notifications_read(
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    from sqlalchemy import update
    db.execute(
        update(Notification).where(
            Notification.organization_id == current_user.organization_id,
            Notification.user_id == current_user.id,
            Notification.is_read.is_(False)
        ).values(is_read=True)
    )
    db.commit()
    return {"status": "ok"}


@router.patch("/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    notification = db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.organization_id == current_user.organization_id,
            Notification.user_id == current_user.id,
        )
    ).scalar_one_or_none()

    if not notification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    notification.is_read = True  # type: ignore
    db.commit()
    db.refresh(notification)
    return notification
