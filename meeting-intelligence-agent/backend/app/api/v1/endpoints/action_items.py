"""Action Items API Endpoints"""
from typing import List, Optional
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, case, cast, String
from pydantic import BaseModel, ConfigDict

from app.core.database import get_db
from app.models.action_item import ActionItem
from app.models.meeting import Meeting
from app.models.user import User
from app.api.v1.endpoints.auth import require_org_member
from app.services.notifications import create_notification

router = APIRouter()


class ActionItemCreate(BaseModel):
    title: str
    description: Optional[str] = None
    meeting_id: UUID
    assigned_to_user_id: Optional[UUID] = None
    due_date: Optional[datetime] = None
    priority: str = "medium"
    category: Optional[str] = None
    tags: List[str] = []


class ActionItemUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    assigned_to_user_id: Optional[UUID] = None
    due_date: Optional[datetime] = None
    priority: Optional[str] = None


class ActionItemResponse(BaseModel):
    id: UUID
    title: str
    description: Optional[str]
    meeting_id: UUID
    category: Optional[str]
    assigned_to_user_id: Optional[UUID]
    status: str
    priority: str
    due_date: Optional[datetime]
    completed_at: Optional[datetime]
    extraction_method: Optional[str]
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


@router.post("/", response_model=ActionItemResponse, status_code=status.HTTP_201_CREATED)
async def create_action_item(
    item_data: ActionItemCreate,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Create new action item"""
    # Ensure the meeting exists and user has access to it
    meeting_result = db.execute(
        select(Meeting).where(
            Meeting.id == item_data.meeting_id,
            Meeting.organization_id == current_user.organization_id,
            Meeting.deleted_at.is_(None),
        )
    )
    meeting = meeting_result.scalar_one_or_none()

    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found",
        )

    if current_user.role != "admin" and (
        meeting.organizer_id != current_user.id
        and str(current_user.id) not in (meeting.attendee_ids or [])
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied for this meeting",
        )

    assigned_to_user_id = item_data.assigned_to_user_id or current_user.id

    if assigned_to_user_id:
        assigned_user = db.execute(
            select(User).where(
                User.id == assigned_to_user_id,
                User.organization_id == current_user.organization_id,
            )
        ).scalar_one_or_none()
        if not assigned_user:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Assigned user must belong to your organization")

    action_item = ActionItem(
        **item_data.model_dump(exclude={"assigned_to_user_id"}),
        organization_id=current_user.organization_id,
        assigned_to_user_id=assigned_to_user_id,
        extraction_method="manual",
    )
    
    db.add(action_item)
    if assigned_to_user_id and assigned_to_user_id != current_user.id:
        create_notification(
            db,
            user_id=assigned_to_user_id,
            organization_id=current_user.organization_id,
            notification_type="task_assigned",
            message=f"You were assigned a task: {action_item.title}",
            notification_metadata={
                "action_item_id": str(action_item.id),
                "meeting_id": str(action_item.meeting_id),
            },
        )
    db.commit()
    db.refresh(action_item)

    # Auto-create Linear issue if user has Linear connected
    linear_creds = (current_user.integrations or {}).get("linear", {})
    if linear_creds.get("api_key"):
        try:
            import asyncio
            import httpx

            async def _push_to_linear():
                async with httpx.AsyncClient() as client:
                    r = await client.post(
                        "https://api.linear.app/graphql",
                        headers={"Authorization": linear_creds["api_key"], "Content-Type": "application/json"},
                        json={"query": "{ teams { nodes { id name } } }"},
                    )
                teams = r.json().get("data", {}).get("teams", {}).get("nodes", [])
                if not teams:
                    return
                team_id = teams[0]["id"]
                mutation = """
                mutation CreateIssue($teamId: String!, $title: String!, $description: String) {
                  issueCreate(input: { teamId: $teamId, title: $title, description: $description }) {
                    success
                    issue { id identifier url }
                  }
                }
                """
                desc = action_item.description or f"Action item from meeting"
                async with httpx.AsyncClient() as client:
                    await client.post(
                        "https://api.linear.app/graphql",
                        headers={"Authorization": linear_creds["api_key"], "Content-Type": "application/json"},
                        json={"query": mutation, "variables": {"teamId": team_id, "title": action_item.title, "description": desc}},
                    )

            asyncio.create_task(_push_to_linear())
        except Exception:
            pass  # Linear sync is non-fatal

    return action_item


@router.get("/", response_model=List[ActionItemResponse])
async def list_action_items(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """List user's action items"""
    query = select(ActionItem).filter(
        or_(
            ActionItem.assigned_to_user_id == current_user.id,
            cast(ActionItem.collaborator_ids, String).contains(str(current_user.id)),
        )
    )
    query = query.where(ActionItem.organization_id == current_user.organization_id)

    if current_user.role == "admin":
        query = select(ActionItem).where(ActionItem.organization_id == current_user.organization_id)
    
    if status:
        query = query.where(ActionItem.status == status)
    if priority:
        query = query.where(ActionItem.priority == priority)

    query = query.order_by(
        case((ActionItem.status == "completed", 1), else_=0),
        ActionItem.due_date.is_(None),
        ActionItem.due_date.asc(),
        ActionItem.created_at.desc(),
    ).offset(skip).limit(limit)
    
    result = db.execute(query)
    items = result.scalars().all()
    
    return items


@router.get("/{item_id}", response_model=ActionItemResponse)
async def get_action_item(
    item_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Get action item details"""
    result = db.execute(
        select(ActionItem).where(
            ActionItem.id == item_id,
            ActionItem.organization_id == current_user.organization_id,
        )
    )
    item = result.scalar_one_or_none()
    
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action item not found",
        )

    if (
        current_user.role != "admin"
        and item.assigned_to_user_id != current_user.id
        and str(current_user.id) not in (item.collaborator_ids or [])
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    
    return item


@router.patch("/{item_id}", response_model=ActionItemResponse)
async def update_action_item(
    item_id: UUID,
    update_data: ActionItemUpdate,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Update action item"""
    result = db.execute(
        select(ActionItem).where(
            ActionItem.id == item_id,
            ActionItem.organization_id == current_user.organization_id,
        )
    )
    item = result.scalar_one_or_none()
    
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action item not found",
        )

    if (
        current_user.role != "admin"
        and item.assigned_to_user_id != current_user.id
        and str(current_user.id) not in (item.collaborator_ids or [])
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    
    previous_assignee = item.assigned_to_user_id

    # Update fields
    for field, value in update_data.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    
    # Handle completion
    if update_data.status == "completed":
        if not item.completed_at:  # type: ignore
            item.completed_at = datetime.utcnow()  # type: ignore
    elif update_data.status and item.completed_at:
        item.completed_at = None  # type: ignore
    
    if (
        update_data.assigned_to_user_id
        and update_data.assigned_to_user_id != previous_assignee
        and update_data.assigned_to_user_id != current_user.id
    ):
        create_notification(
            db,
            user_id=update_data.assigned_to_user_id,
            organization_id=current_user.organization_id,
            notification_type="task_assigned",
            message=f"You were assigned a task: {item.title}",
            notification_metadata={
                "action_item_id": str(item.id),
                "meeting_id": str(item.meeting_id),
            },
        )

    db.commit()
    db.refresh(item)
    
    return item


@router.post("/{item_id}/complete", response_model=ActionItemResponse)
async def complete_action_item(
    item_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Mark action item as complete"""
    result = db.execute(
        select(ActionItem).where(
            ActionItem.id == item_id,
            ActionItem.organization_id == current_user.organization_id,
        )
    )
    item = result.scalar_one_or_none()
    
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action item not found",
        )

    if (
        current_user.role != "admin"
        and item.assigned_to_user_id != current_user.id
        and str(current_user.id) not in (item.collaborator_ids or [])
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    
    item.status = "completed"  # type: ignore
    item.completed_at = datetime.utcnow()  # type: ignore
    
    db.commit()
    db.refresh(item)
    
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_action_item(
    item_id: UUID,
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Delete an action item (admin or assignee only)"""
    item = db.execute(
        select(ActionItem).where(
            ActionItem.id == item_id,
            ActionItem.organization_id == current_user.organization_id,
        )
    ).scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action item not found")

    if (
        current_user.role != "admin"
        and item.assigned_to_user_id != current_user.id
        and str(current_user.id) not in (item.collaborator_ids or [])
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    db.delete(item)
    db.commit()


@router.delete("/", status_code=status.HTTP_200_OK)
async def bulk_delete_action_items(
    ids: List[UUID] = Query(...),
    current_user: User = Depends(require_org_member),
    db: Session = Depends(get_db),
):
    """Bulk delete action items by list of IDs (admin or assignee)"""
    items = db.execute(
        select(ActionItem).where(
            ActionItem.id.in_(ids),
            ActionItem.organization_id == current_user.organization_id,
        )
    ).scalars().all()

    for item in items:
        if (
            current_user.role != "admin"
            and item.assigned_to_user_id != current_user.id
            and str(current_user.id) not in (item.collaborator_ids or [])
        ):
            continue
        db.delete(item)

    db.commit()
    return {"deleted": len(items)}


@router.post("/reminders/trigger", tags=["Action Items"])
async def trigger_reminders(
    current_user: User = Depends(require_org_member),
):
    """Manually trigger the action item reminder task (admin only, for testing)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    from app.tasks.action_item_reminders import send_action_item_reminders
    task = send_action_item_reminders.delay()
    return {"status": "queued", "task_id": str(task.id)}
