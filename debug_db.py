import asyncio
from sqlalchemy import text
from app.db.database import engine

async def check_db():
    print("Attempting to connect to database...")
    try:
        async with engine.connect() as conn:
            print("Connection successful!")
            result = await conn.execute(text("SELECT 1"))
            print(f"Query Result: {result.scalar()}")
    except Exception as e:
        print(f"CONNECTION FAILED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(check_db())
