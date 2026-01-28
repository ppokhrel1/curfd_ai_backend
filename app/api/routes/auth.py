from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.db.session import get_db
from app.models.user import User as UserModel
from app.schemas.auth import LoginRequest
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


@router.post("/login", response_model=UserRead)
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
    return user
