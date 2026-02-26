from sqlalchemy import Column, String, JSON, DateTime

from app.db.base import Base


class Assembly(Base):
    __tablename__ = "assemblies"

    id = Column(String, primary_key=True, index=True)
    chat_id = Column(String, index=True, nullable=False)
    name = Column(String, default="My Assembly")
    # JSON array of serialised AssemblyPart objects from the frontend store
    parts = Column(JSON, default=[])

    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)
