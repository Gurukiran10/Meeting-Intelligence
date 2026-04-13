"""
Meeting processing utilities: reprocess, retry, retention enforcement
"""
from datetime import datetime, timedelta
import logging
from typing import Optional
from sqlalchemy.orm import Session
import hashlib

from app.models import (
    Meeting, 
    NotificationIdempotency,
    RetentionPolicy, 
    RetentionLog,
    AuditLog,
    User
)


logger = logging.getLogger(__name__)


def create_idempotency_key(
    meeting_id: str,
    notification_type: str,
    provider: str,
    recipient: str,
) -> str:
    """
    Create idempotency key for notification to prevent duplicates.
    Format: {meeting_id}:{notification_type}:{provider}:{recipient_hash}
    """
    recipient_hash = hashlib.sha256(recipient.encode()).hexdigest()[:16]
    return f"{meeting_id}:{notification_type}:{provider}:{recipient_hash}"


def get_or_create_notification_tracking(
    db: Session,
    meeting_id: str,
    notification_type: str,
    provider: str,
    recipient: str,
    payload: dict = None,
) -> NotificationIdempotency:
    """
    Get or create notification idempotency record.
    Prevents duplicate notifications from being sent.
    """
    idempotency_key = create_idempotency_key(
        meeting_id=meeting_id,
        notification_type=notification_type,
        provider=provider,
        recipient=recipient,
    )
    
    # Check if already exists
    existing = db.query(NotificationIdempotency).filter(
        NotificationIdempotency.idempotency_key == idempotency_key
    ).first()
    
    if existing:
        logger.info(f"Notification already tracked: {idempotency_key}, status={existing.status}")
        return existing
    
    # Calculate payload hash for deduplication
    payload_hash = None
    if payload:
        payload_str = str(sorted(payload.items()))
        payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()
    
    # Create new record
    notification = NotificationIdempotency(
        idempotency_key=idempotency_key,
        notification_type=notification_type,
        meeting_id=meeting_id,
        provider=provider,
        recipient=recipient,
        payload_hash=payload_hash,
        payload=payload,
        status="pending",
    )
    
    db.add(notification)
    db.commit()
    
    logger.info(f"Notification tracking created: {idempotency_key}")
    return notification


def mark_notification_sent(
    db: Session,
    idempotency_key: str,
    response_status: str = "200",
    response_body: str = None,
):
    """Mark a notification as successfully sent"""
    notification = db.query(NotificationIdempotency).filter(
        NotificationIdempotency.idempotency_key == idempotency_key
    ).first()
    
    if notification:
        notification.status = "sent"
        notification.sent_at = datetime.utcnow()
        notification.response_status = response_status
        notification.response_body = response_body
        notification.attempt_count += 1
        db.commit()
        logger.info(f"Notification marked sent: {idempotency_key}")


def mark_notification_failed(
    db: Session,
    idempotency_key: str,
    error_message: str = None,
    response_status: str = None,
    retry_in_minutes: int = 5,
):
    """Mark notification as failed and schedule retry"""
    notification = db.query(NotificationIdempotency).filter(
        NotificationIdempotency.idempotency_key == idempotency_key
    ).first()
    
    if notification:
        notification.status = "failed" if notification.attempt_count >= 3 else "retrying"
        notification.response_status = response_status
        if error_message:
            notification.response_body = str(error_message)[:500]
        notification.retry_at = datetime.utcnow() + timedelta(minutes=retry_in_minutes)
        notification.attempt_count += 1
        db.commit()
        logger.warning(f"Notification failed: {idempotency_key}, attempt {notification.attempt_count}")


def queue_meeting_for_reprocessing(
    db: Session,
    meeting_id: str,
    user: User,
    reason: str = "manual_reprocess",
):
    """Queue a meeting for reprocessing (re-transcription and re-analysis)"""
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if not meeting:
        logger.error(f"Meeting not found: {meeting_id}")
        return False
    
    # Reset status
    old_status = {
        "status": meeting.status,
        "transcription_status": meeting.transcription_status,
        "analysis_status": meeting.analysis_status,
    }
    
    meeting.status = "processing"
    meeting.transcription_status = "processing"
    meeting.analysis_status = "pending"
    
    db.commit()
    
    # Log the reprocess action
    audit_log = AuditLog(
        user_id=user.id,
        action="meeting_reprocessed",
        resource_type="meeting",
        resource_id=meeting_id,
        description=f"Meeting reprocessed. Reason: {reason}",
        old_value=old_status,
        new_value={
            "status": meeting.status,
            "transcription_status": meeting.transcription_status,
            "analysis_status": meeting.analysis_status,
        },
    )
    db.add(audit_log)
    db.commit()
    
    logger.info(f"Meeting queued for reprocessing: {meeting_id}, reason: {reason}")
    return True


def enforce_retention_policy(db: Session, dry_run: bool = False):
    """
    Apply retention policies: delete old recordings/transcripts based on policy.
    This should be run daily as a background job.
    """
    logger.info("Starting retention policy enforcement")
    
    # Get active retention policies
    policies = db.query(RetentionPolicy).filter(
        RetentionPolicy.is_active == True
    ).all()
    
    if not policies:
        logger.info("No active retention policies found")
        return
    
    deletion_count = 0
    
    for policy in policies:
        logger.info(f"Applying policy: {policy.name}")
        
        # For now, apply default policy to all meetings not matching specific criteria
        if policy.applies_to_type != "all_meetings":
            logger.debug(f"Skipping policy {policy.name} (not all_meetings)")
            continue
        
        # Check recordings
        cutoff_date = datetime.utcnow() - timedelta(days=policy.recording_retention_days)
        
        old_meetings = db.query(Meeting).filter(
            Meeting.recording_path.isnot(None),
            Meeting.created_at < cutoff_date,
            Meeting.deleted_at.is_(None),
        ).all()
        
        for meeting in old_meetings:
            if meeting.recording_path:
                logger.info(f"Deleting recording for meeting {meeting.id} (created {meeting.created_at})")
                
                # Log deletion
                if not dry_run:
                    retention_log = RetentionLog(
                        resource_type="recording",
                        resource_id=meeting.id,
                        resource_name=meeting.title,
                        meeting_id=meeting.id,
                        reason="retention_policy_enforcement",
                        policy_id=policy.id,
                        deleted_by="system",
                        data_size_mb=meeting.recording_size_mb,
                    )
                    db.add(retention_log)
                    
                    # In production, actually delete the file from storage
                    # For now, just mark as deleted
                    meeting.recording_path = None
                    meeting.recording_url = None
                    
                    deletion_count += 1
        
        db.commit()
    
    if dry_run:
        logger.info(f"[DRY RUN] Would delete {deletion_count} recordings")
    else:
        logger.info(f"Retention enforcement complete: {deletion_count} resources deleted")


def get_failed_meetings_for_retry(db: Session, max_attempts: int = 3) -> list:
    """Get meetings that failed and are eligible for automatic retry"""
    failed_meetings = db.query(Meeting).filter(
        (Meeting.transcription_status == "failed") |
        (Meeting.analysis_status == "failed")
    ).all()
    
    # In production, track retry attempts somewhere and check against max_attempts
    return failed_meetings
