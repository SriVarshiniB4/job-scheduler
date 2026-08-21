"""
Job submission API.

Deliberately thin — this layer just validates input and calls the
repository functions we already built (and already tested against a
live DB). No business logic lives here; that's on purpose, so the same
core logic is guaranteed identical whether a job comes in via this API
or via a script, which matters for correctness.
"""

import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.repositories.job_repository import create_job, get_job, list_jobs
from app.api.schemas import JobCreateRequest, JobCreateResponse, JobDetail, JobSummary

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobCreateResponse, status_code=201)
async def submit_job(req: JobCreateRequest, session: AsyncSession = Depends(get_session)):
    # Dependency validation: every job_id in depends_on must actually exist,
    # otherwise this job would silently never become claimable (the claim
    # query's NOT EXISTS check can't distinguish "dependency not done yet"
    # from "dependency doesn't exist" — both just mean "don't claim me").
    for dep_id in req.depends_on:
        dep = await get_job(session, dep_id)
        if dep is None:
            raise HTTPException(422, f"depends_on job {dep_id} does not exist")

    row, was_created = await create_job(
        session,
        job_type=req.job_type,
        payload=req.payload,
        priority=req.priority,
        idempotency_key=req.idempotency_key,
        run_after=req.run_after,
        max_attempts=req.max_attempts,
        depends_on=req.depends_on,
    )
    return JobCreateResponse(**dict(row), was_duplicate=not was_created)


@router.get("/{job_id}", response_model=JobDetail)
async def get_job_detail(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    row = await get_job(session, job_id)
    if row is None:
        raise HTTPException(404, "job not found")
    return JobDetail(**dict(row))


@router.get("", response_model=list[JobSummary])
async def list_all_jobs(
    status: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    rows = await list_jobs(session, status=status, limit=limit)
    return [JobSummary(**dict(r)) for r in rows]
