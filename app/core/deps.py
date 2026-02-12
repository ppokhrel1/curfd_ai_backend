from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.jwt import decode_access_token, token_hash
from app.db.session import get_db, get_db_async
from app.models.revoked_token import RevokedToken
from sqlalchemy.ext.asyncio import AsyncSession

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

async def get_current_user_id_async(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db_async),  # Use AsyncSession
) -> str:
    token = credentials.credentials
    user_id = decode_access_token(token)
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid token"
        )

    # Hash the token to check revocation
    token_digest = token_hash(token)
    
    # ASYNC DATABASE CHECK
    stmt = select(RevokedToken).where(RevokedToken.token_hash == token_digest)
    result = await db.execute(stmt)
    revoked = result.scalar_one_or_none()
    
    if revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Token revoked"
        )

    return user_id