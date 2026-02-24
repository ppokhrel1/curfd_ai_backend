from sqlalchemy import Column, String, Integer, Float, JSON, DateTime, ForeignKey

from app.db.base import Base

class ScadJob(Base):
    __tablename__ = "scad_jobs"

    id = Column(String, primary_key=True, index=True) # e.g., UUID
    chat_id = Column(String, index=True) 
    status = Column(String, default="Pending") # Pending, Processing, Completed, Failed
    worker_task_id = Column(String, nullable=True)
    
    # Inputs
    openscad_code = Column(String)
    parameters = Column(JSON) # Store list of dicts
    generations = Column(Integer)
    population_size = Column(Integer)
    
    # Outputs
    optimized_parameters = Column(JSON, nullable=True)
    fitness_score = Column(Float, nullable=True)
    result_url = Column(String, nullable=True)
    error = Column(String, nullable=True)
    
    # Timestamps
    started_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)