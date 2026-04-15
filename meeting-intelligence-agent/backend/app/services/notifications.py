"""
Helpers for internal in-app notifications.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.notification import Notification


def create_notification(
    db: Session,
    *,
    user_id: Any,
    organization_id: Any,
    notification_type: str,
    message: str,
    notification_metadata: Optional[dict[str, Any]] = None,
) -> Notification:
    notification = Notification(
        user_id=user_id,
        organization_id=organization_id,
        type=notification_type,
        message=message,
        is_read=False,
        notification_metadata=notification_metadata or {},
    )
    db.add(notification)
    db.flush()
    return notification
