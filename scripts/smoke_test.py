"""
Manual smoke test — proves the repository functions actually work
against a real Postgres instance, not just that they import cleanly.

This is NOT the concurrent stress test from the plan (that's a
separate, bigger piece of work for later). This just proves the basic
happy path and one failure path work end to end.
"""

import asyncio
import uuid
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.repositories.job_repository import (
    claim_next_job, renew_lease, mark_running, mark_completed,
    mark_failed_or_dead_letter,
)


async def main():
    async with AsyncSessionLocal() as session:
        # 1. Register a fake worker (FK requirement for locked_by)
        worker_id = uuid.uuid4()
        await session.execute(
            text("INSERT INTO workers (id, label, status) VALUES (:id, 'test-worker', 'active')"),
            {"id": worker_id},
        )
        await session.commit()

        # 2. Insert three test jobs with different priorities
        job_ids = []
        for i, priority in enumerate([0, 5, 2]):
            jid = uuid.uuid4()
            job_ids.append(jid)
            await session.execute(
                text("""INSERT INTO jobs (id, job_type, payload, status, priority, attempts, max_attempts)
                        VALUES (:id, 'send_email', :payload, 'queued', :priority, 0, 5)"""),
                {"id": jid, "payload": f'{{"to": "test{i}@example.com"}}', "priority": priority},
            )
        await session.commit()
        print(f"Inserted 3 jobs with priorities [0, 5, 2]")

        # 3. Claim — should get the priority=5 job FIRST (highest priority wins)
        claimed = await claim_next_job(session, worker_id)
        print(f"Claimed job: priority={claimed['priority']} (expected: 5)")
        assert claimed["priority"] == 5, "Priority ordering is broken!"

        # 4. Renew lease — should succeed since we still own it
        ok = await renew_lease(session, claimed["id"], worker_id)
        print(f"Lease renewal while owning job: {ok} (expected: True)")
        assert ok is True

        # 5. Simulate another worker trying to steal it — should get nothing
        #    since our lease hasn't expired
        other_worker_id = uuid.uuid4()
        await session.execute(
            text("INSERT INTO workers (id, label, status) VALUES (:id, 'test-worker-2', 'active')"),
            {"id": other_worker_id},
        )
        await session.commit()
        stolen = await claim_next_job(session, other_worker_id)
        # Should claim the NEXT job (priority=2), not steal our active lease
        print(f"Second worker claimed: priority={stolen['priority']} (expected: 2, NOT the priority=5 job)")
        assert stolen["priority"] == 2, "Lease protection is broken — job was stolen while lease active!"

        # 6. Mark our job completed
        await mark_completed(session, claimed["id"])
        row = (await session.execute(
            text("SELECT status FROM jobs WHERE id = :id"), {"id": claimed["id"]}
        )).mappings().first()
        print(f"Job status after completion: {row['status']} (expected: completed)")
        assert row["status"] == "completed"

        # 7. Test failure -> retry path on the third job
        third = await claim_next_job(session, worker_id)
        await mark_failed_or_dead_letter(session, third["id"], "simulated failure")
        row = (await session.execute(
            text("SELECT status, attempts, run_after > now() as delayed FROM jobs WHERE id = :id"),
            {"id": third["id"]},
        )).mappings().first()
        print(f"Job after 1st failure: status={row['status']}, attempts={row['attempts']}, "
              f"run_after pushed into future={row['delayed']} (expected: queued, 1, True)")
        assert row["status"] == "queued"
        assert row["delayed"] is True

        print("\n✅ ALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
