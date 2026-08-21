"""
FastAPI app entrypoint. Run with:
    uvicorn app.main:app --reload --port 8000
"""

from fastapi import FastAPI
from app.api.jobs import router as jobs_router
from app.api.events import router as events_router
from app.api.dev import router as dev_router

app = FastAPI(
    title="Distributed Job Scheduler",
    description="Postgres-backed job queue with lease-based fault tolerance.",
    version="0.1.0",
)

app.include_router(jobs_router)
app.include_router(events_router)
app.include_router(dev_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
