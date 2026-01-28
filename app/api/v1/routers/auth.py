"""
Authentication API Routes

Implements:
- POST /auth/register - User registration (Req 1.1, 1.5)
- POST /auth/login - User login (Req 1.2)
- GET /auth/me - Get current user profile (Req 1.4)
"""
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_access_token,
    get_password_hash,
    verify_password,
    get_current_user,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from app.db.database import get_session
from app.repositories.user_repo import UserRepository
from app.models.user import User, UserCreate, UserResponse

router = APIRouter()


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenWithUser(Token):
    user: UserResponse


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@router.post("/register", response_model=TokenWithUser, status_code=status.HTTP_201_CREATED)
async def register(
    user_create: UserCreate,
    session: Annotated[AsyncSession, Depends(get_session)]
):
    """
    Register a new user.
    
    Returns 409 Conflict if email already exists.
    Returns JWT token on success.
    """
    user_repo = UserRepository(session)
    
    # Check for existing email (Req 1.5)
    existing_user = await user_repo.get_by_email(user_create.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered"
        )
    
    # Create user with hashed password
    hashed_password = get_password_hash(user_create.password)
    db_user = User(
        email=user_create.email,
        name=user_create.name,
        hashed_password=hashed_password
    )
    user = await user_repo.create(db_user)
    
    # Create access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        subject=user.email, expires_delta=access_token_expires
    )
    
    return TokenWithUser(
        access_token=access_token,
        user=UserResponse(id=user.id, email=user.email, name=user.name, created_at=user.created_at)
    )


@router.post("/login", response_model=Token)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: Annotated[AsyncSession, Depends(get_session)]
):
    """
    Authenticate user and return JWT token.
    
    Returns 401 Unauthorized if credentials are invalid.
    """
    user_repo = UserRepository(session)
    
    user = await user_repo.get_by_email(form_data.username)
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        subject=user.email, expires_delta=access_token_expires
    )
    
    return Token(access_token=access_token)


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: Annotated[User, Depends(get_current_user)]
):
    """
    Get current authenticated user's profile.
    
    Requires valid JWT token.
    Returns 401 if token is invalid or expired (Req 1.3, 1.4).
    """
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        created_at=current_user.created_at
    )
