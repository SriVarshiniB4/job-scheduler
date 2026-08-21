"""
The worker loop.

This is the process you'll run multiple copies of (worker-1, worker-2,
worker-3...) to actually get concurrent execution. Each instance:

  1. Registers itself in the `workers` table
  2. Loops forever: try to claim a job -> if got one, run it (with a
     heartbeat renewing the lease in the background) -> mark it
     completed/failed -> repeat. If nothing to claim, sleep briefly
     and try again (this is polling — simple and fine at this scale;
     see the note at the bottom about why we're NOT using LISTEN/NOTIFY
     for claiming, only for the dashboard).

The heartbeat runs as a SEPARATE asyncio task alongside the job
execution, not sequentially — this is the part people get wrong. If you
awaited "do the job, THEN renew," a job running longer than the lease
duration would just lose its lease and get stolen, even though the
worker is perfectly healthy. The heartbeat has to run concurrently.
"""

import asyncio
import uuid
import logging
from datetime import datetime, timezone

from app.db.session import AsyncSessionLocal
from app.core.config import settings
from app.repositories.job_repository import (
    claim_next_job,
    renew_lease,
    mark_running,
    mark_completed,
    mark_failed_or_dead_letter,
)
from app.workers.handlers import dispatch, UnknownJobTypeError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("worker")

POLL_INTERVAL_SECONDS = 1.0


class LeaseLostError(Exception):
    """Raised internally when a heartbeat renewal fails — meaning some
    other worker has already reclaimed this job. The job execution task
    is cancelled when this happens; we must not let it finish and mark
    itself completed after losing ownership."""


async def _heartbeat_loop(job_id: uuid.UUID, worker_id: uuid.UUID, stop_event: asyncio.Event):
    """
    Runs alongside job execution. Every HEARTBEAT_INTERVAL_SECONDS,
    renews the lease. If renewal ever fails (returns False), that means
    we no longer own the job — set stop_event so the main task cancels
    execution immediately instead of finishing work it doesn't own
    anymore.
    """
    while not stop_event.is_set():
        await asyncio.sleep(settings.HEARTBEAT_INTERVAL_SECONDS)
        if stop_event.is_set():
            return
        async with AsyncSessionLocal() as session:
            still_owned = await renew_lease(session, job_id, worker_id)
        if not still_owned:
            logger.warning(f"Lease lost for job {job_id} — signaling execution to abort")
            stop_event.set()
            return


async def _run_one_job(worker_id: uuid.UUID, job_row) -> None:
    job_id = job_row["id"]
    job_type = job_row["job_type"]
    payload = job_row["payload"]

    async with AsyncSessionLocal() as session:
        await mark_running(session, job_id)

    stop_event = asyncio.Event()
    heartbeat_task = asyncio.create_task(_heartbeat_loop(job_id, worker_id, stop_event))
    # Inject identity into payload for handlers that need it (currently
    # only the test-only counted_task handler uses these). Real handlers
    # can ignore these extra keys.
    dispatch_payload = {**payload, "job_id": str(job_id), "_worker_id": str(worker_id)}
    execution_task = asyncio.create_task(dispatch(job_type, dispatch_payload))

    try:
        # Race the actual job execution against the lease being lost.
        # whichever finishes/fires first decides the outcome.
        done, pending = await asyncio.wait(
            {execution_task, asyncio.create_task(stop_event.wait())},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if stop_event.is_set() and execution_task not in done:
            # Lease was lost WHILE the job was still running. We must not
            # let this worker mark the job completed — cancel and bail.
            execution_task.cancel()
            logger.error(f"Job {job_id} aborted mid-execution: lease was lost to another worker")
            for p in pending:
                p.cancel()
            return  # deliberately do NOT call mark_completed/mark_failed —
                    # the worker that stole the lease owns this job's fate now

        # Execution finished before any lease loss — surface its result/exception
        exc = execution_task.exception()
        for p in pending:
            p.cancel()

        async with AsyncSessionLocal() as session:
            if exc is None:
                await mark_completed(session, job_id)
                logger.info(f"Job {job_id} ({job_type}) completed")
            else:
                await mark_failed_or_dead_letter(session, job_id, failure_reason=str(exc))
                logger.warning(f"Job {job_id} ({job_type}) failed: {exc}")

    finally:
        stop_event.set()
        heartbeat_task.cancel()


async def _register_worker(worker_id: uuid.UUID, label: str) -> None:
    import os
    async with AsyncSessionLocal() as session:
        from sqlalchemy import text
        await session.execute(
            text("INSERT INTO workers (id, label, pid, status, last_heartbeat) "
                 "VALUES (:id, :label, :pid, 'active', now())"),
            {"id": worker_id, "label": label, "pid": os.getpid()},
        )
        await session.commit()


async def run_worker(label: str = None) -> None:
    worker_id = uuid.uuid4()
    label = label or f"worker-{str(worker_id)[:8]}"
    await _register_worker(worker_id, label)
    logger.info(f"{label} started (id={worker_id})")

    while True:
        async with AsyncSessionLocal() as session:
            job_row = await claim_next_job(session, worker_id)

        if job_row is None:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue

        logger.info(f"{label} claimed job {job_row['id']} ({job_row['job_type']})")
        await _run_one_job(worker_id, job_row)


if __name__ == "__main__":
    import sys
    label = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(run_worker(label))

# =============================================================
# Note on polling vs LISTEN/NOTIFY for CLAIMING (not for the dashboard):
#
# We poll every 1s to check for claimable work, rather than using
# Postgres LISTEN/NOTIFY to wake workers instantly on new jobs. This is
# a deliberate simplification: NOTIFY would reduce claim latency from
# ~1s to near-instant, but adds complexity (listener connection
# management, reconnect-on-drop logic) that isn't where this project's
# value is. LISTEN/NOTIFY IS worth using for the DASHBOARD's live
# updates (Day 6-7 of the plan) because sub-second latency there is the
# whole point of a "live" dashboard — but for claiming, 1s polling is a
# reasonable, defensible tradeoff you should be ready to explain, not
# something to apologize for.
# =============================================================
