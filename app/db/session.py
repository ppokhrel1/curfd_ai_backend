from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import settings

if not settings.database_url:
    raise RuntimeError("Database URL is not configured")

engine = create_async_engine(
    settings.database_url, 
    pool_pre_ping=True, 
    future=True
)

SessionLocal = async_sessionmaker(
    autocommit=False, 
    autoflush=False, 
    bind=engine, 
    class_=AsyncSession
)

async def get_db():
    async with SessionLocal() as db:
        try:
            yield db
        finally:
            await db.close()