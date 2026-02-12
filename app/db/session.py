from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import settings

from sqlalchemy.ext.asyncio import create_async_engine

# Make sure the URL starts with postgresql+asyncpg://
engine = create_async_engine(
    settings.database_url, pool_pre_ping=True, future=True,
    connect_args={
        "prepare_threshold": None
    }
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    expire_on_commit=False
)

async def get_db_async() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
