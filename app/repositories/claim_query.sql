-- =============================================================
-- THE CLAIM QUERY — the single most important query in this project.
-- Read this top to bottom before looking at the Python wrapper.
-- =============================================================
--
-- This runs inside a transaction. One worker calls this, gets back
-- (at most) one job row, and by the time the row is returned, that
-- job is ALREADY locked to this worker — no separate "now update it"
-- step needed, which matters because that gap is exactly where race
-- conditions live.

BEGIN;

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
        -- Only unclaimed jobs, OR jobs whose lease has expired while
        -- claimed OR actively running (this second condition is what
        -- makes crash-reclaim work — a claimed/running job with an
        -- expired lease is fair game again, regardless of which of
        -- those two states it was in when its owner died)
        (status = 'queued' OR (status IN ('claimed', 'running') AND lease_expires_at < now()))

        -- Don't touch scheduled/delayed jobs before their time,
        -- and don't touch jobs mid-backoff after a failed attempt
        AND run_after <= now()

        -- Dependency check: exclude any job that has an unfinished
        -- prerequisite. NOT EXISTS a dependency row pointing to a
        -- job that isn't 'completed' yet.
        AND NOT EXISTS (
            SELECT 1
            FROM job_dependencies jd
            JOIN jobs dep ON dep.id = jd.depends_on_job_id
            WHERE jd.job_id = jobs.id
              AND dep.status != 'completed'
        )
    ORDER BY priority DESC, created_at ASC
    LIMIT 1

    -- THIS is the line that makes concurrent workers safe.
    -- FOR UPDATE: lock the selected row so no other transaction can
    --   modify it until this transaction commits or rolls back.
    -- SKIP LOCKED: if a row is already locked by another worker's
    --   in-flight transaction, don't wait for it and don't error —
    --   just skip past it and consider the next candidate row.
    --
    -- Without SKIP LOCKED, every worker querying at the same moment
    -- would queue up waiting for the lock, serializing all your
    -- "concurrent" workers into one at a time. WITH it, N workers
    -- can each grab a DIFFERENT job in the same instant, safely.
    FOR UPDATE SKIP LOCKED
)
RETURNING id, job_type, payload, attempts, priority;

COMMIT;

-- =============================================================
-- Why this can't double-claim, walked through explicitly:
--
-- Worker A and Worker B both run this at the exact same millisecond.
-- 1. Both inner SELECTs run. Say both would naturally pick job #42
--    (same ORDER BY, same top candidate).
-- 2. Postgres serializes the actual row-locking at the storage layer:
--    ONE of them (say A) actually acquires the lock on #42 first.
-- 3. B's FOR UPDATE SKIP LOCKED sees #42 is locked -> skips it ->
--    naturally falls through to the NEXT best candidate, #43.
-- 4. A commits, #42 is now status='claimed', locked_by=A.
-- 5. B commits, #43 is now status='claimed', locked_by=B.
--
-- Neither worker ever saw the other's in-progress claim as available.
-- No application-level locking (mutex, Redis lock, etc.) needed at all
-- — Postgres's MVCC + row locking does the entire job for free.
-- =============================================================
