from datetime import datetime, timedelta, timezone
from hashlib import sha256
import uuid

from jose import JWTError, jwt

from app.core.config import settings


def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    to_encode = {"sub": subject, "exp": expire, "jti": str(uuid.uuid4())}
    return jwt.encode(to_encode, settings.secret_key or "dev-secret", algorithm=settings.algorithm)


def create_access_token_with_exp(subject: str, expires_at: datetime) -> str:
    to_encode = {"sub": subject, "exp": expires_at, "jti": str(uuid.uuid4())}
    return jwt.encode(to_encode, settings.secret_key or "dev-secret", algorithm=settings.algorithm)


def decode_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(
            token, settings.secret_key or "dev-secret", algorithms=[settings.algorithm]
        )
    except JWTError:
        return None
    subject = payload.get("sub")
    if not subject:
        return None
    return subject


def decode_token_payload(token: str, verify_exp: bool = True) -> dict | None:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key or "dev-secret",
            algorithms=[settings.algorithm],
            options={"verify_exp": verify_exp},
        )
    except JWTError:
        return None
    return payload


def token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()
