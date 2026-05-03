"""Mentions API Endpoints"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import require_org_member
from app.core.database import get_db
from app.models.mention import Mention
from app.models.notification import Notification
from app.models.user import User

router = APIRouter()


class MentionResponse(BaseModel):
    id: UUID
    meeting_id: UUID
    user_id: UUID
    mention_type: str
    mentioned_text: str
    relevance_score: Optional[float]
    urgency_score: Optional[float]
    sentiment: Optional[str]
    notification_read: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.get("/", response_model=List[MentionResponse])
async def list_mentions(
    skip: int = 0,
    limit: int = 100,
    unread_only: bool = False,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """List mentions for current user"""
    query = select(Mention).where(
        Mention.organization_id == current_user.organization_id,
        Mention.user_id == current_user.id,
    )
    if unread_only:
        query = query.where(Mention.notification_read.is_(False))

    query = query.order_by(desc(Mention.created_at)).offset(skip).limit(limit)
    result = db.execute(query)
    return result.scalars().all()


@router.get("/{mention_id}", response_model=MentionResponse)
async def get_mention(
    mention_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Get single mention"""
    result = db.execute(
        select(Mention).where(
            Mention.id == mention_id,
            Mention.organization_id == current_user.organization_id,
            Mention.user_id == current_user.id,
        )
    )
    mention = result.scalar_one_or_none()
    if not mention:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mention not found")
    return mention


@router.delete("/{mention_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mention(
    mention_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Delete a single mention"""
    mention = db.execute(
        select(Mention).where(
            Mention.id == mention_id,
            Mention.organization_id == current_user.organization_id,
            Mention.user_id == current_user.id,
        )
    ).scalar_one_or_none()
    if not mention:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mention not found")
    db.delete(mention)
    db.commit()


@router.delete("/", status_code=status.HTTP_200_OK)
async def bulk_delete_mentions(
    ids: List[UUID] = Query(...),
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Bulk delete mentions by list of IDs"""
    mentions = db.execute(
        select(Mention).where(
            Mention.id.in_(ids),
            Mention.organization_id == current_user.organization_id,
            Mention.user_id == current_user.id,
        )
    ).scalars().all()
    for mention in mentions:
        db.delete(mention)
    db.commit()
    return {"deleted": len(mentions)}


@router.post("/{mention_id}/read", response_model=MentionResponse)
async def mark_mention_read(
    mention_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Mark mention as read"""
    result = db.execute(
        select(Mention).where(
            Mention.id == mention_id,
            Mention.organization_id == current_user.organization_id,
            Mention.user_id == current_user.id,
        )
    )
    mention = result.scalar_one_or_none()
    if not mention:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mention not found")

    mention.notification_read = True  # type: ignore
    mention.notification_read_at = datetime.utcnow()  # type: ignore

    notifications = db.execute(
        select(Notification).where(
            Notification.organization_id == current_user.organization_id,
            Notification.user_id == current_user.id,
            Notification.type == "mention",
        )
    ).scalars().all()
    for notification in notifications:
        metadata = getattr(notification, "notification_metadata", {}) or {}
        if str(metadata.get("mention_id", "")) == str(mention.id):
            notification.is_read = True  # type: ignore

    db.commit()
    db.refresh(mention)
    return mention
