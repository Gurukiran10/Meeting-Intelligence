"""
Authentication Endpoints
"""
from datetime import datetime, timedelta
import re
from typing import Optional
from uuid import UUID
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings

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


# ─── Google OAuth ─────────────────────────────────────────────────────────────

async def get_valid_google_token(user: User, db: Session) -> str:
    """Return a valid access token, refreshing via stored credentials if expired."""
    if not user.google_connected or not user.google_refresh_token:
        raise HTTPException(status_code=400, detail="Google not connected for this user")

    if (
        user.google_access_token
        and user.google_token_expiry
        and user.google_token_expiry > datetime.utcnow() + timedelta(seconds=60)
    ):
        return user.google_access_token

    google = (user.integrations or {}).get("google", {})
    client_id = google.get("client_id") or settings.GOOGLE_CLIENT_ID
    client_secret = google.get("client_secret") or settings.GOOGLE_CLIENT_SECRET

    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Google OAuth credentials not stored; reconnect Google")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": user.google_refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Google token refresh failed: {resp.text}")

    data = resp.json()
    user.google_access_token = data["access_token"]
    user.google_token_expiry = datetime.utcnow() + timedelta(seconds=data.get("expires_in", 3600) - 30)
    db.commit()
    return user.google_access_token


import logging as _logger
_log = _logger.getLogger(__name__)


class GoogleLoginRequest(BaseModel):
    client_id: str


@router.post("/google/login")
async def google_login(
    req: GoogleLoginRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    redirect_uri = settings.GOOGLE_REDIRECT_URI
    if not redirect_uri:
        raise HTTPException(status_code=500, detail="GOOGLE_REDIRECT_URI not configured in backend/.env")

    # Persist client_id so the callback can retrieve it without JWT
    import copy
    integrations = copy.deepcopy(current_user.integrations or {})
    integrations.setdefault("google", {})["client_id"] = req.client_id
    current_user.integrations = integrations
    db.commit()

    params = {
        "client_id": req.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,           # always backend URL from env
        "scope": "openid email profile https://www.googleapis.com/auth/calendar.readonly",
        "access_type": "offline",
        "prompt": "consent",
        "state": str(current_user.id),
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    _log.info(f"[Google OAuth login] user={current_user.id} redirect_uri={redirect_uri}")
    return {"auth_url": auth_url}


@router.get("/google/callback")
async def google_callback(
    code: str,
    state: str,
    db: Session = Depends(get_db),
):
    """Browser redirect from Google — no JWT required, user identified via state."""
    import copy

    user = db.execute(select(User).where(User.id == state)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    client_secret = (settings.GOOGLE_CLIENT_SECRET or "").strip()
    if not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Server misconfigured: GOOGLE_CLIENT_SECRET missing. Set it in backend/.env and restart.",
        )

    google = (user.integrations or {}).get("google", {})
    client_id = (google.get("client_id") or settings.GOOGLE_CLIENT_ID or "").strip()
    redirect_uri = (settings.GOOGLE_REDIRECT_URI or "").strip()

    masked_secret = client_secret[:4] + "****" + client_secret[-4:] if len(client_secret) >= 8 else "****"
    masked_id = client_id[:8] + "..." + client_id[-8:] if len(client_id) >= 16 else client_id
    _log.info(
        f"[Google OAuth callback] user={user.id} "
        f"client_id={masked_id} (len={len(client_id)}) "
        f"client_secret={masked_secret} (len={len(client_secret)}) "
        f"redirect_uri={redirect_uri}"
    )

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
        )

    _log.info(f"[Google OAuth callback] token exchange status={token_resp.status_code}")

    if token_resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Google token exchange failed: {token_resp.text}")

    token_data = token_resp.json()
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)

    async with httpx.AsyncClient() as client:
        info_resp = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    google_email = info_resp.json().get("email") if info_resp.status_code == 200 else None

    expiry_dt = datetime.utcnow() + timedelta(seconds=expires_in - 30)
    expiry_iso = expiry_dt.replace(microsecond=0).isoformat()

    # Write to dedicated DB columns (used by auth.get_valid_google_token)
    user.google_access_token = access_token
    if refresh_token:
        user.google_refresh_token = refresh_token
    user.google_token_expiry = expiry_dt
    user.google_email = google_email
    user.google_connected = True

    # ALSO write to user.integrations["google"] so all integration endpoints
    # (_get_google_access_token, test_google, sync, calendar fetch) can read them.
    integrations = copy.deepcopy(user.integrations or {})
    google = integrations.get("google", {})
    google["access_token"] = access_token
    google["refresh_token"] = refresh_token or google.get("refresh_token")
    google["oauth_refresh_token"] = refresh_token or google.get("oauth_refresh_token")
    google["token_expires_at"] = expiry_iso
    google["client_id"] = client_id
    google["client_secret"] = client_secret
    google["redirect_uri"] = redirect_uri
    google["method"] = "oauth"
    google["calendar_id"] = google.get("calendar_id") or "primary"
    integrations["google"] = google
    user.integrations = integrations

    db.commit()
    return RedirectResponse(url=f"{settings.FRONTEND_URL}/integrations?google=connected")
