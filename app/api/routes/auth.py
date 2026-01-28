from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, timezone

from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id, security
from app.core.jwt import create_access_token, token_hash
from app.core.security import hash_password, verify_password
from app.db.session import get_db
from app.models.revoked_token import RevokedToken
from app.models.user import User as UserModel
from app.schemas.auth import LoginRequest, TokenResponse
from jose import JWTError, jwt
from app.core.config import settings
from app.schemas.user import UserCreate, UserRead

router = APIRouter()


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register_user(payload: UserCreate, db: Session = Depends(get_db)):
    existing = (
        db.query(UserModel)
        .filter(
            (UserModel.username == payload.username)
            | (UserModel.email == payload.email)
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="User already exists")

    user = UserModel(
        username=payload.username,
        email=payload.email,
        display_name=payload.display_name,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = (
        db.query(UserModel)
        .filter(
            (UserModel.username == payload.username_or_email)
            | (UserModel.email == payload.username_or_email)
        )
        .first()
    )
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(subject=user.id)
    return TokenResponse(access_token=token, user_id=user.id)


@router.get("/me", response_model=dict)
def get_current_user(
    user_id: str = Depends(get_current_user_id),
):
    return {"user_id": user_id}


@router.get("/logout", response_model=dict)
def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token, settings.secret_key or "dev-secret", algorithms=[settings.algorithm]
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    exp = payload.get("exp")
    expires_at = (
        datetime.fromtimestamp(exp, tz=timezone.utc) if isinstance(exp, (int, float)) else None
    )
    token_digest = token_hash(token)
    exists = db.query(RevokedToken).filter(RevokedToken.token_hash == token_digest).first()
    if not exists:
        db.add(RevokedToken(token_hash=token_digest, expires_at=expires_at))
        db.commit()
    return {"status": "logged_out"}
