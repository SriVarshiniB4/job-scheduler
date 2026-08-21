"""
Request/response schemas for the jobs API.

Kept deliberately separate from the DB models (app/models/db.py) — the
API's shape and the database's shape are allowed to diverge, and coupling
them tightly is a common mistake that makes both harder to change later.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class JobCreateRequest(BaseModel):
    job_type: str = Field(..., examples=["send_email", "flaky_task", "slow_task"])
    payload: dict = Field(default_factory=dict)
    priority: int = Field(default=0, ge=0, le=100)
    idempotency_key: Optional[str] = Field(
        default=None,
        description="If provided and a job with this key already exists, "
                     "that existing job is returned instead of creating a duplicate.",
    )
    run_after: Optional[datetime] = Field(
        default=None, description="Delay execution until this time. Omit to run ASAP."
    )
    max_attempts: int = Field(default=5, ge=1, le=20)
    depends_on: list[uuid.UUID] = Field(
        default_factory=list,
        description="Job IDs that must reach status=completed before this job is claimable.",
    )


class JobCreateResponse(BaseModel):
    id: uuid.UUID
    job_type: str
    status: str
    priority: int
    created_at: datetime
    was_duplicate: bool = Field(
        description="True if this returned an existing job matched by idempotency_key, "
                     "rather than creating a new one."
    )


class JobDetail(BaseModel):
    id: uuid.UUID
    job_type: str
    payload: dict
    status: str
    priority: int
    attempts: int
    max_attempts: int
    idempotency_key: Optional[str]
    run_after: datetime
    locked_by: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime


class JobSummary(BaseModel):
    id: uuid.UUID
    job_type: str
    status: str
    priority: int
    attempts: int
    created_at: datetime
