"""
Authentication routes — register, login, and profile retrieval.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.models.user import User
from core.services.auth_service import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from core.settings import settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# -- Pydantic schemas --


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str = Field(..., min_length=1, max_length=255)
    role: str | None = None  # only honoured when the caller is admin


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str | None
    role: str
    org_id: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RegisterResponse(BaseModel):
    user: UserResponse
    token: Token


# -- Routes --


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[Any] = None,
):
    """Register a new user account.

    * Self-registration always assigns the **viewer** role.
    * An authenticated **admin** may pass a custom ``role``.
    """
    # Check for duplicate email
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalars().first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    # Determine role
    bootstrap_admins = {
        e.strip().lower()
        for e in settings.ADMIN_EMAILS.split(",")
        if e.strip()
    }
    role = "viewer"
    if body.role and current_user is not None and current_user.role == "admin":
        role = body.role
    elif body.email.lower() in bootstrap_admins:
        role = "admin"

    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=role,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    access_token = create_access_token(data={"sub": user.email})

    return RegisterResponse(
        user=UserResponse.model_validate(user),
        token=Token(access_token=access_token),
    )


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Authenticate with email (username field) and password.

    Returns a bearer JWT on success.
    """
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalars().first()

    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    access_token = create_access_token(data={"sub": user.email})
    return Token(access_token=access_token)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    """Return the profile of the currently authenticated user."""
    return UserResponse.model_validate(current_user)
