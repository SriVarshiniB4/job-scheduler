"""
Chaos test — proves the fault-tolerance claim directly, not by inference.

Sequence:
  1. Seed ONE job of type 'chaos_task' with a long duration (25s) —
     comfortably longer than the lease duration (15s default), so if a
     worker dies, the lease WILL expire before that worker could have
     naturally finished.
  2. Spawn 2 real worker processes.
  3. Wait until chaos_log shows the job was 'started' by one of them.
  4. Identify exactly which OS process that was (via workers.label) and
     kill -9 it — a hard kill, no graceful shutdown, simulating a real
     crash (server dies, OOM kill, etc). This also kills its heartbeat,
     since the heartbeat is a task inside that same process.
  5. Wait past the lease expiry window.
  6. Confirm the SURVIVING worker reclaims the job (a second 'started'
     event in chaos_log, from a DIFFERENT worker_id) and eventually
     finishes it.
  7. Assert: exactly one 'finished_work' event total (no double
     completion), the job's final status is 'completed', and the two
     'started' events came from two different worker_ids.
"""

import asyncio
import os
import signal
import subprocess
import sys
import time
import uuid
from sqlalchemy import text

sys.path.insert(0, ".")
from app.db.session import AsyncSessionLocal

JOB_DURATION_SECONDS = 25
LEASE_DURATION_SECONDS = 15  # must match app/core/config.py default
MAX_WAIT_SECONDS = 90


async def setup():
    async with AsyncSessionLocal() as session:
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS chaos_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                job_id UUID NOT NULL,
                worker_id UUID NOT NULL,
                event TEXT NOT NULL,
                at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        await session.execute(text("TRUNCATE chaos_log, jobs, workers CASCADE"))
        await session.commit()

        job_id = uuid.uuid4()
        await session.execute(
            text("""INSERT INTO jobs (id, job_type, payload, status, priority, attempts, max_attempts)
                    VALUES (:id, 'chaos_task', :payload, 'queued', 0, 0, 3)"""),
            {"id": job_id, "payload": f'{{"duration_seconds": {JOB_DURATION_SECONDS}}}'},
        )
        await session.commit()
    print(f"Seeded 1 chaos_task job (id={job_id}, duration={JOB_DURATION_SECONDS}s)")
    return job_id


async def get_starter_worker_id(job_id):
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            text("SELECT worker_id FROM chaos_log WHERE job_id = :jid AND event = 'started' LIMIT 1"),
            {"jid": job_id},
        )).mappings().first()
        return row["worker_id"] if row else None


async def get_worker_label(worker_id):
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            text("SELECT label FROM workers WHERE id = :wid"), {"wid": worker_id}
        )).mappings().first()
        return row["label"] if row else None


async def get_chaos_log(job_id):
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            text("SELECT worker_id, event, at FROM chaos_log WHERE job_id = :jid ORDER BY at"),
            {"jid": job_id},
        )).mappings().all()
        return list(rows)


async def get_job_status(job_id):
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            text("SELECT status FROM jobs WHERE id = :jid"), {"jid": job_id}
        )).mappings().first()
        return row["status"]


async def main():
    job_id = await setup()

    labels = ["chaos-worker-0", "chaos-worker-1"]
    print(f"Spawning 2 real worker PROCESSES: {labels}")
    procs = {}
    for label in labels:
        p = subprocess.Popen(
            [sys.executable, "app/workers/worker.py", label],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        procs[label] = p

    # Step 1: wait for the job to actually be claimed and started
    print("Waiting for a worker to claim and start the job...")
    starter_worker_id = None
    start = time.time()
    while time.time() - start < 15:
        starter_worker_id = await get_starter_worker_id(job_id)
        if starter_worker_id:
            break
        await asyncio.sleep(1)

    if starter_worker_id is None:
        print("❌ Job was never started — cannot run chaos test.")
        for p in procs.values():
            p.terminate()
        sys.exit(1)

    starter_label = await get_worker_label(starter_worker_id)
    print(f"Job started by worker_id={starter_worker_id} (label={starter_label})")

    # Step 2: kill -9 that exact process
    victim_proc = procs[starter_label]
    print(f"💀 Killing {starter_label} (pid={victim_proc.pid}) with SIGKILL...")
    os.kill(victim_proc.pid, signal.SIGKILL)
    victim_proc.wait(timeout=5)
    print(f"{starter_label} is dead. Its heartbeat has stopped.")

    # Step 3: wait past lease expiry + reclaim + eventual completion
    print(f"Waiting up to {MAX_WAIT_SECONDS}s for the surviving worker to reclaim and finish it...")
    start = time.time()
    final_status = None
    while time.time() - start < MAX_WAIT_SECONDS:
        await asyncio.sleep(3)
        final_status = await get_job_status(job_id)
        elapsed = int(time.time() - start)
        print(f"  [{elapsed}s] job status = {final_status}")
        if final_status == "completed":
            break

    # Cleanup remaining process
    for label, p in procs.items():
        if p.poll() is None:
            p.terminate()

    # Step 4: verify
    log = await get_chaos_log(job_id)
    print("\n=== chaos_log for this job ===")
    for row in log:
        print(f"  {row['at']}  worker={row['worker_id']}  event={row['event']}")

    started_events = [r for r in log if r["event"] == "started"]
    finished_events = [r for r in log if r["event"] == "finished_work"]
    distinct_starters = {r["worker_id"] for r in started_events}

    print(f"\n=== RESULTS ===")
    print(f"Final job status: {final_status} (expected: completed)")
    print(f"Distinct workers that started this job: {len(distinct_starters)} (expected: 2)")
    print(f"Total 'finished_work' events: {len(finished_events)} (expected: 1 — no double completion)")

    success = (
        final_status == "completed"
        and len(distinct_starters) == 2
        and len(finished_events) == 1
    )
    print(f"\n{'✅ CRASH RECOVERY CONFIRMED — lease expired, job reclaimed, no double completion' if success else '❌ TEST FAILED'}")
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
