"""
Repository layer for job claiming and lease management.

This wraps the raw SQL in claim_query.sql (read that file first — this
module assumes you understand WHY the query is shaped the way it is;
here we're just concerned with wiring it up correctly in Python).
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings


CLAIM_QUERY = text("""
    UPDATE jobs
    SET
        status = 'claimed',
        locked_by = :worker_id,
        locked_at = now(),
        lease_expires_at = now() + (:lease_seconds * interval '1 second'),
        attempts = attempts + 1
    WHERE id = (
        SELECT id
        FROM jobs
        WHERE
            (status = 'queued' OR (status IN ('claimed', 'running') AND lease_expires_at < now()))
            AND run_after <= now()
            AND NOT EXISTS (
                SELECT 1
                FROM job_dependencies jd
                JOIN jobs dep ON dep.id = jd.depends_on_job_id
                WHERE jd.job_id = jobs.id
                  AND dep.status != 'completed'
            )
        ORDER BY priority DESC, created_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING id, job_type, payload, attempts, priority;
""")


async def claim_next_job(session: AsyncSession, worker_id: uuid.UUID):
    """
    Attempts to claim exactly one job for this worker.

    Returns the claimed job row (as a Row/mapping), or None if there's
    nothing claimable right now (empty queue, everything scheduled for
    later, or every available job is currently locked by another worker).

    IMPORTANT: the caller is responsible for the transaction boundary.
    In practice that means: don't call this inside a session that's
    already mid-transaction doing unrelated work, and DO commit right
    after this call so the row lock (held only until commit) is released
    promptly — holding it open longer than necessary just adds
    contention for other workers polling at the same time.
    """
    result = await session.execute(
        CLAIM_QUERY,
        {"worker_id": worker_id, "lease_seconds": settings.LEASE_DURATION_SECONDS},
    )
    row = result.mappings().first()
    await session.commit()
    return row


async def create_job(
    session: AsyncSession,
    job_type: str,
    payload: dict,
    priority: int = 0,
    idempotency_key: str | None = None,
    run_after: datetime | None = None,
    max_attempts: int = 5,
    depends_on: list[uuid.UUID] | None = None,
):
    """
    Inserts a new job. If idempotency_key collides with an existing job,
    returns the EXISTING job instead of raising — this is what makes
    idempotency actually useful to a caller: submit the same logical
    request twice (e.g. a retried HTTP call) and get back the same job,
    not a duplicate and not an error the caller has to handle specially.
    """
    import json as _json

    job_id = uuid.uuid4()
    run_after = run_after or datetime.now(timezone.utc)

    if idempotency_key:
        existing = (await session.execute(
            text("SELECT id, job_type, status, priority, created_at FROM jobs WHERE idempotency_key = :key"),
            {"key": idempotency_key},
        )).mappings().first()
        if existing:
            return existing, False  # False = not newly created

    await session.execute(
        text("""
            INSERT INTO jobs
                (id, job_type, payload, status, priority, idempotency_key,
                 run_after, attempts, max_attempts, created_at, updated_at)
            VALUES
                (:id, :job_type, :payload, 'queued', :priority, :idempotency_key,
                 :run_after, 0, :max_attempts, now(), now())
        """),
        {
            "id": job_id,
            "job_type": job_type,
            "payload": _json.dumps(payload),
            "priority": priority,
            "idempotency_key": idempotency_key,
            "run_after": run_after,
            "max_attempts": max_attempts,
        },
    )

    if depends_on:
        for dep_id in depends_on:
            await session.execute(
                text("INSERT INTO job_dependencies (job_id, depends_on_job_id) VALUES (:jid, :did)"),
                {"jid": job_id, "did": dep_id},
            )

    await session.commit()

    row = (await session.execute(
        text("SELECT id, job_type, status, priority, created_at FROM jobs WHERE id = :id"),
        {"id": job_id},
    )).mappings().first()
    return row, True  # True = newly created


async def get_job(session: AsyncSession, job_id: uuid.UUID):
    return (await session.execute(
        text("""SELECT id, job_type, payload, status, priority, attempts, max_attempts,
                        idempotency_key, run_after, locked_by, created_at, updated_at
                 FROM jobs WHERE id = :id"""),
        {"id": job_id},
    )).mappings().first()


async def list_jobs(session: AsyncSession, status: str | None = None, limit: int = 50):
    query = "SELECT id, job_type, status, priority, attempts, created_at FROM jobs"
    params = {"limit": limit}
    if status:
        query += " WHERE status = :status"
        params["status"] = status
    query += " ORDER BY created_at DESC LIMIT :limit"
    return (await session.execute(text(query), params)).mappings().all()


async def renew_lease(session: AsyncSession, job_id: uuid.UUID, worker_id: uuid.UUID) -> bool:
    """
    Heartbeat: push a claimed job's lease forward while the worker is
    still actively processing it. Called on a timer (every
    HEARTBEAT_INTERVAL_SECONDS) from inside the job's execution loop.

    Returns False if the renewal didn't affect any row — meaning this
    worker no longer owns the job (most likely: it already lost the
    lease to another worker because it renewed too slowly, e.g. due to
    a long GC pause or the process being overloaded). In that case the
    worker MUST stop processing the job immediately — it's not the
    owner anymore, and finishing the work now would race with whoever
    reclaimed it. This "stop on failed renewal" check is what actually
    prevents double-processing after a false-positive crash detection.
    """
    result = await session.execute(
        text("""
            UPDATE jobs
            SET lease_expires_at = now() + (:lease_seconds * interval '1 second')
            WHERE id = :job_id AND locked_by = :worker_id AND status IN ('claimed', 'running')
        """),
        {
            "job_id": job_id,
            "worker_id": worker_id,
            "lease_seconds": settings.LEASE_DURATION_SECONDS,
        },
    )
    await session.commit()
    return result.rowcount > 0


async def mark_running(session: AsyncSession, job_id: uuid.UUID) -> None:
    await session.execute(
        text("UPDATE jobs SET status = 'running' WHERE id = :job_id"),
        {"job_id": job_id},
    )
    await session.commit()


async def mark_completed(session: AsyncSession, job_id: uuid.UUID) -> None:
    await session.execute(
        text("""
            UPDATE jobs
            SET status = 'completed', locked_by = NULL, lease_expires_at = NULL
            WHERE id = :job_id
        """),
        {"job_id": job_id},
    )
    await session.commit()


async def mark_failed_or_dead_letter(
    session: AsyncSession, job_id: uuid.UUID, failure_reason: str
) -> None:
    """
    On failure: if attempts < max_attempts, requeue with exponential
    backoff (run_after pushed forward). Otherwise, move to dead_letter_jobs.

    Backoff formula: 2^attempts seconds, capped implicitly by max_attempts
    being small (default 5 -> worst case 32s before final attempt).
    """
    row = (
        await session.execute(
            text("SELECT attempts, max_attempts, job_type, payload FROM jobs WHERE id = :job_id"),
            {"job_id": job_id},
        )
    ).mappings().first()

    if row is None:
        return

    if row["attempts"] >= row["max_attempts"]:
        await session.execute(
            text("""
                INSERT INTO dead_letter_jobs
                    (id, original_job_id, job_type, payload, failure_reason, attempts_made, moved_at)
                VALUES
                    (gen_random_uuid(), :job_id, :job_type, :payload, :reason, :attempts, now())
            """),
            {
                "job_id": job_id,
                "job_type": row["job_type"],
                "payload": row["payload"],
                "reason": failure_reason,
                "attempts": row["attempts"],
            },
        )
        await session.execute(
            text("UPDATE jobs SET status = 'dead_letter', locked_by = NULL WHERE id = :job_id"),
            {"job_id": job_id},
        )
    else:
        backoff_seconds = 2 ** row["attempts"]
        await session.execute(
            text("""
                UPDATE jobs
                SET status = 'queued',
                    locked_by = NULL,
                    lease_expires_at = NULL,
                    run_after = now() + (:backoff * interval '1 second')
                WHERE id = :job_id
            """),
            {"job_id": job_id, "backoff": backoff_seconds},
        )

    await session.commit()
