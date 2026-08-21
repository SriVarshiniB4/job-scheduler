"""
Concurrency stress test.

This is the test that actually justifies every claim this project makes
about correctness under concurrency. Everything before this (the smoke
test) only ran ONE worker at a time — that proves the logic is right in
isolation, but says nothing about what happens when N workers hit the
same queue simultaneously, which is the entire point of FOR UPDATE
SKIP LOCKED existing at all.

What this does:
  1. Creates an execution_log table (test-only, not part of the real schema)
  2. Seeds NUM_JOBS jobs of type 'counted_task'
  3. Spawns NUM_WORKERS real OS processes (not asyncio tasks — actual
     separate Python processes, each with its own DB connection, which
     is the realistic deployment scenario) all running worker.py
     concurrently against the same Postgres instance
  4. Waits for the queue to drain
  5. Verifies:
       a. execution_log has exactly NUM_JOBS rows total
       b. NO job_id appears more than once in execution_log
          (this is the direct, non-inferential proof that no job was
          ever executed twice)
       c. every job's final status in `jobs` is 'completed'
"""

import asyncio
import subprocess
import sys
import time
import uuid
from sqlalchemy import text

sys.path.insert(0, ".")
from app.db.session import AsyncSessionLocal

NUM_JOBS = 500
NUM_WORKERS = 8
MAX_WAIT_SECONDS = 90


async def setup():
    async with AsyncSessionLocal() as session:
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS execution_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                job_id UUID NOT NULL,
                worker_id UUID NOT NULL,
                executed_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        await session.execute(text("TRUNCATE execution_log, jobs, workers CASCADE"))
        await session.commit()

        job_ids = [uuid.uuid4() for _ in range(NUM_JOBS)]
        for jid in job_ids:
            await session.execute(
                text("""INSERT INTO jobs (id, job_type, payload, status, priority, attempts, max_attempts)
                        VALUES (:id, 'counted_task', '{}', 'queued', 0, 0, 3)"""),
                {"id": jid},
            )
        await session.commit()
    print(f"Seeded {NUM_JOBS} jobs.")
    return job_ids


async def check_results(job_ids):
    async with AsyncSessionLocal() as session:
        total_executions = (await session.execute(
            text("SELECT count(*) FROM execution_log")
        )).scalar()

        duplicates = (await session.execute(text("""
            SELECT job_id, count(*) as cnt FROM execution_log
            GROUP BY job_id HAVING count(*) > 1
        """))).mappings().all()

        completed_count = (await session.execute(
            text("SELECT count(*) FROM jobs WHERE status = 'completed'")
        )).scalar()

        not_completed = (await session.execute(
            text("SELECT id, status FROM jobs WHERE status != 'completed'")
        )).mappings().all()

    print(f"\n=== RESULTS ===")
    print(f"Jobs seeded:          {NUM_JOBS}")
    print(f"Total executions logged: {total_executions}")
    print(f"Jobs with status=completed: {completed_count}")
    print(f"Duplicate executions (job_id appearing >1x): {len(duplicates)}")
    if duplicates:
        print(f"  ⚠️  DUPLICATES FOUND: {duplicates[:5]}")
    if not_completed:
        print(f"  ⚠️  {len(not_completed)} jobs never completed: {list(not_completed)[:5]}")

    success = (
        total_executions == NUM_JOBS
        and len(duplicates) == 0
        and completed_count == NUM_JOBS
        and len(not_completed) == 0
    )
    print(f"\n{'✅ NO DOUBLE-PROCESSING — SKIP LOCKED IS WORKING CORRECTLY' if success else '❌ TEST FAILED'}")
    return success


async def main():
    job_ids = await setup()

    print(f"Spawning {NUM_WORKERS} real worker PROCESSES...")
    procs = []
    for i in range(NUM_WORKERS):
        p = subprocess.Popen(
            [sys.executable, "app/workers/worker.py", f"stress-worker-{i}"],
            stdout=subprocess.DEVNULL,  # quiet — we only care about the DB end-state
            stderr=subprocess.DEVNULL,
        )
        procs.append(p)

    print(f"Waiting for queue to drain (checking every 2s, max {MAX_WAIT_SECONDS}s)...")
    start = time.time()
    while time.time() - start < MAX_WAIT_SECONDS:
        await asyncio.sleep(2)
        remaining = await _remaining_count()
        print(f"  remaining queued/claimed jobs: {remaining}")
        if remaining == 0:
            break

    print("Stopping worker processes...")
    for p in procs:
        p.terminate()
    for p in procs:
        p.wait(timeout=5)

    success = await check_results(job_ids)
    sys.exit(0 if success else 1)


async def _remaining_count():
    async with AsyncSessionLocal() as session:
        return (await session.execute(
            text("SELECT count(*) FROM jobs WHERE status IN ('queued', 'claimed', 'running')")
        )).scalar()


if __name__ == "__main__":
    asyncio.run(main())
