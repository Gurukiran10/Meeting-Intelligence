"""
Authentication Endpoints
"""
from datetime import datetime
import re
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
    verify_password,
)
from app.models.organization import Organization
from app.models.user import User

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

ACCESS_COOKIE_NAME = "syncminds_access_token"
REFRESH_COOKIE_NAME = "syncminds_refresh_token"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:120] or "workspace"


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    secure = False
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        access_token,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=60 * 30,
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh_token,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=60 * 60 * 24 * 7,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/")


def _serialize_user(user: User, organization: Optional[Organization]) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "username": user.username,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": user.is_active,
        "organization": (
            {
                "id": str(organization.id),
                "name": organization.name,
                "slug": organization.slug,
            }
            if organization
            else None
        ),
    }


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    username: str
    full_name: str
    role: Optional[str] = None
    is_active: bool
    organization: Optional[OrganizationResponse] = None


class SignupRequest(BaseModel):
    email: EmailStr
    username: str
    full_name: str
    password: str = Field(min_length=8)
    organization_name: Optional[str] = None
    organization_slug: Optional[str] = None
    create_organization: bool = True

    @field_validator("organization_slug")
    @classmethod
    def normalize_slug(cls, value: Optional[str]) -> Optional[str]:
        if not value:
            return value
        return _slugify(value)


class TokenResponse(BaseModel):
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    user: UserResponse


class JoinOrganizationRequest(BaseModel):
    organization_slug: str

    @field_validator("organization_slug")
    @classmethod
    def normalize_slug(cls, value: str) -> str:
        return _slugify(value)


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    existing_user = db.execute(
        select(User).where(
            (User.email == payload.email) | (User.username == payload.username)
        )
    ).scalar_one_or_none()
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User with this email or username already exists")

    organization: Optional[Organization] = None
    if payload.create_organization:
        org_name = (payload.organization_name or "").strip()
        if not org_name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="organization_name is required when creating an organization")

        slug = payload.organization_slug or _slugify(org_name)
        if db.execute(select(Organization).where(Organization.slug == slug)).scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Organization slug already exists")

        organization = Organization(name=org_name, slug=slug)
        db.add(organization)
        db.flush()
        role = "admin"
    else:
        join_slug = payload.organization_slug or ""
        organization = db.execute(select(Organization).where(Organization.slug == join_slug)).scalar_one_or_none()
        if not organization:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
        role = "member"

    user = User(
        organization_id=organization.id,
        email=payload.email,
        username=payload.username,
        full_name=payload.full_name,
        hashed_password=get_password_hash(payload.password),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    access_token = create_access_token(data={"sub": str(user.id), "org": str(user.organization_id), "role": user.role})
    refresh_token = create_refresh_token(data={"sub": str(user.id), "org": str(user.organization_id), "role": user.role})
    _set_auth_cookies(response, access_token, refresh_token)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": _serialize_user(user, organization),
    }


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: SignupRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    """Backward-compatible alias for signup."""
    return await signup(payload=payload, response=response, db=db)


@router.post("/login", response_model=TokenResponse)
async def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    try:
        user = db.execute(
            select(User).where(User.username == form_data.username)
        ).scalar_one_or_none()
        print("user:", user)
    except Exception as exc:
        print("login db error:", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to process login right now",
        )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        password_matches = verify_password(form_data.password, user.hashed_password)
        print("password check:", password_matches)
    except Exception as exc:
        print("password verify error:", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to process login right now",
        )

    if not password_matches:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")

    organization = db.execute(select(Organization).where(Organization.id == user.organization_id)).scalar_one_or_none()
    access_token = create_access_token(data={"sub": str(user.id), "org": str(user.organization_id), "role": user.role})
    refresh_token = create_refresh_token(data={"sub": str(user.id), "org": str(user.organization_id), "role": user.role})
    _set_auth_cookies(response, access_token, refresh_token)

    user.last_login = datetime.utcnow()  # type: ignore
    db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": _serialize_user(user, organization),
    }


@router.post("/refresh", response_model=TokenResponse)
async def refresh_session(
    response: Response,
    refresh_token: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
    db: Session = Depends(get_db),
):
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")

    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user_id = payload.get("sub")
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    organization = db.execute(select(Organization).where(Organization.id == user.organization_id)).scalar_one_or_none()
    new_access_token = create_access_token(data={"sub": str(user.id), "org": str(user.organization_id), "role": user.role})
    new_refresh_token = create_refresh_token(data={"sub": str(user.id), "org": str(user.organization_id), "role": user.role})
    _set_auth_cookies(response, new_access_token, new_refresh_token)

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "user": _serialize_user(user, organization),
    }


async def get_current_user(
    request: Request,
    token_from_bearer: Optional[str] = Depends(oauth2_scheme),
    access_token_cookie: Optional[str] = Cookie(default=None, alias=ACCESS_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> User:
    token = access_token_cookie or token_from_bearer
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")

    try:
        payload = decode_token(token)
        print("decoded token:", payload)
    except ValueError as exc:
        print("token decode error:", str(exc))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")

    try:
        user_lookup_id = UUID(str(user_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")

    try:
        user = db.execute(select(User).where(User.id == user_lookup_id)).scalar_one_or_none()
        print("user fetched:", user)
    except Exception as exc:
        print("get_current_user db error:", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to load current user",
        )

    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    request.state.current_user = user
    return user


def require_org_member(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not linked to an organization")
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if str(current_user.role or "").lower() != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    organization = db.execute(select(Organization).where(Organization.id == current_user.organization_id)).scalar_one_or_none()
    return _serialize_user(current_user, organization)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response):
    _clear_auth_cookies(response)
    return None


@router.get("/organizations/{slug}", response_model=OrganizationResponse)
async def get_organization_by_slug(
    slug: str,
    db: Session = Depends(get_db),
):
    organization = db.execute(select(Organization).where(Organization.slug == _slugify(slug))).scalar_one_or_none()
    if not organization:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return organization
