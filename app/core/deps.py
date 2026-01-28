from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.jwt import decode_access_token, token_hash
from app.db.session import get_db
from app.models.revoked_token import RevokedToken

security = HTTPBearer()


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> str:
    token = credentials.credentials
    user_id = decode_access_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    token_digest = token_hash(token)
    revoked = db.query(RevokedToken).filter(RevokedToken.token_hash == token_digest).first()
    if revoked:
        raise HTTPException(status_code=401, detail="Token revoked")

    return user_id
