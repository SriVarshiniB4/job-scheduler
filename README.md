# Distributed Job Scheduler + Observability Dashboard

A Postgres-backed job queue with lease-based fault tolerance, built to
understand — not just use — the mechanics that libraries like Sidekiq,
Celery, and Temporal hide behind an API.

## What's actually proven (not just claimed)

These aren't aspirational — each was run for real during development,
against a live local Postgres instance:

- **Concurrency correctness**: 8 real worker OS processes against 500
  seeded jobs, verified via a dedicated execution log — **0 duplicate
  executions, all 500 completed exactly once.**
- **Crash recovery**: a worker process was `SIGKILL`ed mid-job; the
  lease expired ~15s later and a *different* worker reclaimed and
  finished the job, with exactly one completion event — no double
  processing. This also caught a real bug (the reclaim query only
  checked `status='claimed'`, missing jobs already in `status='running'`)
  which is documented in `/areas` history and worth being able to
  explain in an interview.
- **Live SSE streaming**: verified a full job lifecycle
  (`queued → claimed → running → completed`) streaming to a live
  `EventSource` connection in real time, driven by a Postgres
  `LISTEN/NOTIFY` trigger — zero polling.
- **HTTP API**: idempotency-key deduplication, dependency validation,
  and true dependency-ordered execution (job B provably waited for job
  A) — all tested over real HTTP requests, not just unit-level calls.

## What's built but not yet run against a real browser

The React dashboard (`frontend/`) builds cleanly (`npm run build`
succeeds with no errors) and is wired for real SSE + REST calls against
the backend — but it was developed in a sandboxed environment without
browser access, so **you should be the first person to actually open it
and click the kill-worker button.** If something's visually off, that's
expected — the visual layer hasn't had human eyes on it yet, unlike the
backend which was exercised repeatedly.

## Setup

### 1. Database
```bash
createdb jobscheduler
# or: psql -c "CREATE DATABASE jobscheduler;"
```

### 2. Backend
```bash
cd job-scheduler
pip install -r requirements.txt
export DATABASE_URL="postgresql+asyncpg://postgres:<password>@localhost:5432/jobscheduler"
export PYTHONPATH=.
python3 scripts/create_tables.py   # creates schema + the NOTIFY trigger
uvicorn app.main:app --reload --port 8000
```

### 3. Run workers (in separate terminals, however many you want)
```bash
export DATABASE_URL="postgresql+asyncpg://postgres:<password>@localhost:5432/jobscheduler"
export PYTHONPATH=.
python3 app/workers/worker.py worker-1
python3 app/workers/worker.py worker-2
```

### 4. Frontend
```bash
cd frontend
npm install
npm run dev   # opens on http://localhost:5173, proxies /api and /events to :8000
```

### 5. Submit a job to watch it flow through live
```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"job_type": "send_email", "payload": {"to": "test@example.com"}}'
```

## Running the test suite

```bash
export DATABASE_URL="postgresql+asyncpg://postgres:<password>@localhost:5432/jobscheduler"
export PYTHONPATH=.

python3 scripts/smoke_test.py    # single-worker happy path, ~5s
python3 scripts/stress_test.py   # 8 workers vs 500 jobs, ~30-60s
python3 scripts/chaos_test.py    # kills a worker mid-job, ~60-90s
```

## Architecture notes worth understanding for interviews

- **`FOR UPDATE SKIP LOCKED`** (`app/repositories/claim_query.sql`) —
  the core concurrency-safety mechanism. Read the comments in that file;
  they walk through exactly why two workers can never claim the same
  job, at the Postgres row-locking level, with no application-side
  mutex needed.
- **Lease/heartbeat** (`app/workers/worker.py`) — a worker's job
  execution races against its own heartbeat task via
  `asyncio.wait(..., FIRST_COMPLETED)`. If the heartbeat fails to renew
  (meaning another worker already reclaimed the job), the losing
  worker's execution is cancelled and it explicitly does NOT touch the
  job's final state — ownership has already moved on.
- **Dead-lettering as a separate table**, not a status flag — keeps the
  hot claim-query path fast by never having to filter out permanently
  failed rows.
- **Job dependencies** are a single `NOT EXISTS` subquery against a
  `job_dependencies` edge table — a minimal DAG scheduler, not a
  separate engine.

## Known limitations (worth naming yourself before an interviewer does)

- The dev-only `/dev/kill-worker/{id}` endpoint sends a raw OS signal by
  pid — fine for a single-machine demo, but explicitly not how you'd do
  this in a real deployment (you'd terminate via your orchestrator).
- Claiming uses 1s polling rather than `LISTEN/NOTIFY` — a deliberate
  simplification; `LISTEN/NOTIFY` is used for the dashboard instead,
  where sub-second feel actually matters.
- No auth on the API — out of scope for what this project is meant to
  demonstrate, but worth saying explicitly rather than leaving unstated.
