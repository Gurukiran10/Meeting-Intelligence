"""
RBAC (Role-Based Access Control) utility functions
"""
from enum import Enum
import logging
from typing import List
from sqlalchemy.orm import Session

from app.models import User, Meeting, AuditLog


logger = logging.getLogger(__name__)


class Permission(str, Enum):
    """Application permissions"""
    # Meeting permissions
    VIEW_MEETING = "view_meeting"
    CREATE_MEETING = "create_meeting"
    UPLOAD_RECORDING = "upload_recording"
    DELETE_MEETING = "delete_meeting"
    VIEW_TRANSCRIPT = "view_transcript"
    
    # Admin permissions
    VIEW_ALL_MEETINGS = "view_all_meetings"
    REPROCESS_MEETING = "reprocess_meeting"
    MANAGE_RETENTION = "manage_retention"
    VIEW_AUDIT_LOGS = "view_audit_logs"
    MANAGE_INTEGRATIONS = "manage_integrations"
    
    # Team/manager permissions
    VIEW_TEAM_MEETINGS = "view_team_meetings"


class Role(str, Enum):
    """User roles"""
    ADMIN = "admin"
    MANAGER = "manager"
    USER = "user"


# Role-to-permission mapping
ROLE_PERMISSIONS = {
    Role.ADMIN: [
        Permission.VIEW_MEETING,
        Permission.CREATE_MEETING,
        Permission.UPLOAD_RECORDING,
        Permission.DELETE_MEETING,
        Permission.VIEW_TRANSCRIPT,
        Permission.VIEW_ALL_MEETINGS,
        Permission.REPROCESS_MEETING,
        Permission.MANAGE_RETENTION,
        Permission.VIEW_AUDIT_LOGS,
        Permission.MANAGE_INTEGRATIONS,
        Permission.VIEW_TEAM_MEETINGS,
    ],
    Role.MANAGER: [
        Permission.VIEW_MEETING,
        Permission.CREATE_MEETING,
        Permission.UPLOAD_RECORDING,
        Permission.VIEW_TRANSCRIPT,
        Permission.VIEW_TEAM_MEETINGS,
    ],
    Role.USER: [
        Permission.VIEW_MEETING,
        Permission.CREATE_MEETING,
        Permission.UPLOAD_RECORDING,
        Permission.VIEW_TRANSCRIPT,
    ],
}


def has_permission(user: User, permission: Permission) -> bool:
    """Check if user has a specific permission"""
    if not user or not user.is_active:
        return False
    
    user_role = Role(user.role) if user.role else Role.USER
    permissions = ROLE_PERMISSIONS.get(user_role, [])
    
    return permission in permissions


def can_view_meeting(user: User, meeting: Meeting) -> bool:
    """Check if user can view this meeting"""
    if not user or not user.is_active:
        return False
    
    # Admins can view all meetings
    if has_permission(user, Permission.VIEW_ALL_MEETINGS):
        return True
    
    # Organizers can view their own meetings
    if meeting.organizer_id and meeting.organizer_id == user.id:
        return True
    
    # Attendees can view meetings they attended
    if meeting.attendee_ids and str(user.id) in [str(aid) for aid in meeting.attendee_ids]:
        return True
    
    return False


def can_upload_recording(user: User, meeting: Meeting) -> bool:
    """Check if user can upload recording for this meeting"""
    if not has_permission(user, Permission.UPLOAD_RECORDING):
        return False
    
    # Only organizer or admin can upload
    if user.is_superuser or user.role == Role.ADMIN:
        return True
    
    return meeting.organizer_id == user.id


def can_reprocess_meeting(user: User) -> bool:
    """Check if user can reprocess a meeting"""
    return has_permission(user, Permission.REPROCESS_MEETING)


from sqlalchemy import cast, String

def get_viewable_meetings(user: User, db: Session) -> List[Meeting]:
    """Get all meetings a user can view"""
    if not user or not user.is_active:
        return []
    
    # Admins can see all meetings
    if has_permission(user, Permission.VIEW_ALL_MEETINGS):
        return db.query(Meeting).filter(Meeting.deleted_at.is_(None)).all()
    
    # Regular users see: meetings they organized + meetings they attended
    user_id_str = str(user.id)
    meetings = db.query(Meeting).filter(
        Meeting.deleted_at.is_(None),
        (Meeting.organizer_id == user.id) |
        cast(Meeting.attendee_ids, String).ilike(f"%{user_id_str}%")
    ).all()
    
    return meetings


def log_audit_event(
    db: Session,
    user: User,
    action: str,
    resource_type: str,
    resource_id = None,
    description: str = None,
    old_value = None,
    new_value = None,
    status: str = "success",
    error_message: str = None,
    ip_address: str = None,
    user_agent: str = None,
):
    """Log an audit event"""
    try:
        audit_log = AuditLog(
            user_id=user.id if user else None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            description=description,
            old_value=old_value,
            new_value=new_value,
            status=status,
            error_message=error_message,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.add(audit_log)
        db.commit()
        logger.info(f"Audit: {action} on {resource_type}:{resource_id} by {user.email if user else 'system'}")
    except Exception as e:
        logger.warning(f"Failed to log audit event: {e}")
        db.rollback()
