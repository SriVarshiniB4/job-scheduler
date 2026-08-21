"""
Job handler registry.

Each job's `job_type` maps to a Python function that actually does the
work. This is intentionally simple (a dict) — the interesting
engineering in this project is the queue mechanics, not the dispatch
pattern, so don't over-build this part.

Add real handlers here as needed. For now, a couple of fake ones so the
worker loop has something to actually execute and so you can test
failure/retry/dead-letter behavior on demand.
"""

import asyncio
import random


async def handler_send_email(payload: dict) -> None:
    await asyncio.sleep(1)  # simulate network I/O
    print(f"[handler] pretending to send email: {payload}")


async def handler_flaky_task(payload: dict) -> None:
    """
    Fails ~50% of the time on purpose. Use this job_type to actually
    exercise your retry/backoff/dead-letter logic in testing — don't
    just trust it works, watch it happen.
    """
    await asyncio.sleep(0.5)
    if random.random() < 0.5:
        raise RuntimeError("simulated flaky failure")
    print(f"[handler] flaky task succeeded: {payload}")


async def handler_slow_task(payload: dict) -> None:
    """
    Runs long enough (default 20s) to cross a 15s lease boundary on
    purpose — use this to test that renew_lease is actually keeping the
    job alive during long-running work, and to test the "kill worker
    mid-job" chaos demo manually before you build the UI button for it.
    """
    duration = payload.get("duration_seconds", 20)
    await asyncio.sleep(duration)
    print(f"[handler] slow task finished after {duration}s: {payload}")


async def handler_counted_task(payload: dict) -> None:
    """
    Used ONLY for the concurrency stress test. Writes a row to
    execution_log every time it actually runs, tagged with which job
    and which worker executed it. If SKIP LOCKED / the lease mechanism
    has a bug that lets two workers run the same job, this is what
    catches it: query execution_log grouped by job_id, and any count > 1
    is direct proof of double-processing, not an inference.
    """
    import asyncio as _asyncio
    from sqlalchemy import text
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""INSERT INTO execution_log (job_id, worker_id, executed_at)
                     VALUES (:job_id, :worker_id, now())"""),
            {"job_id": payload["job_id"], "worker_id": payload["_worker_id"]},
        )
        await session.commit()
    await _asyncio.sleep(0.05)  # small delay so overlapping claims, if any, would be visible


async def handler_chaos_task(payload: dict) -> None:
    """
    Used ONLY for the chaos/crash-recovery test. Logs a 'started' row to
    chaos_log the moment it begins, then sleeps for a while — long enough
    that a test script has time to kill the worker process mid-execution.
    If the job is later completed (by this worker or, after a kill, by
    whichever worker reclaimed it), that's logged too. Comparing
    started vs completed rows for a job_id tells us exactly what
    happened: e.g. 2 starts + 1 completion = worker A got killed after
    starting, worker B reclaimed and finished it — no double-completion.
    """
    import asyncio as _asyncio
    from sqlalchemy import text
    from app.db.session import AsyncSessionLocal

    duration = payload.get("duration_seconds", 12)

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""INSERT INTO chaos_log (job_id, worker_id, event, at)
                     VALUES (:job_id, :worker_id, 'started', now())"""),
            {"job_id": payload["job_id"], "worker_id": payload["_worker_id"]},
        )
        await session.commit()

    await _asyncio.sleep(duration)

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""INSERT INTO chaos_log (job_id, worker_id, event, at)
                     VALUES (:job_id, :worker_id, 'finished_work', now())"""),
            {"job_id": payload["job_id"], "worker_id": payload["_worker_id"]},
        )
        await session.commit()


HANDLERS = {
    "send_email": handler_send_email,
    "flaky_task": handler_flaky_task,
    "slow_task": handler_slow_task,
    "counted_task": handler_counted_task,
    "chaos_task": handler_chaos_task,
}


class UnknownJobTypeError(Exception):
    pass


async def dispatch(job_type: str, payload: dict) -> None:
    handler = HANDLERS.get(job_type)
    if handler is None:
        raise UnknownJobTypeError(f"No handler registered for job_type={job_type!r}")
    await handler(payload)
