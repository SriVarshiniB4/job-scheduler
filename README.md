# Distributed Job Scheduler + Observability Dashboard

A Postgres-backed distributed job queue with lease-based fault tolerance, live SSE-driven observability, and a React dashboard for monitoring worker and job state in real time.

Built from first principles to explore the mechanics that job-queue libraries like Sidekiq, Celery, and Temporal typically abstract away — safe concurrent job claiming, crash recovery, and live state propagation.

## Features

- **Safe concurrent claiming** — multiple worker processes pull from the same job pool with zero duplicate execution, using Postgres `FOR UPDATE SKIP LOCKED`.
- **Automatic crash recovery** — jobs use a renewable lease. If a worker dies mid-job, its lease expires and another worker reclaims the job automatically.
- **Live observability** — job and worker state changes stream to the dashboard in real time via Postgres `LISTEN`/`NOTIFY` and Server-Sent Events, with no client-side polling.
- **Idempotency keys** — duplicate job submissions are safely deduplicated at the API layer.
- **Dead-letter handling** — jobs that exceed their retry limit move to a separate dead-letter table rather than clogging the primary claim query.
- **Job dependencies** — jobs can declare dependencies on other jobs via a `job_dependencies` table, validated at submission time.

## Verified behavior

The following was tested against a live Postgres instance with real, independent worker processes (not mocks):

- 8 concurrent worker processes against 500 seeded jobs: 0 duplicate executions, all 500 completed exactly once.
- A worker process killed mid-job: its lease expired and a different worker reclaimed and completed the job, with exactly one completion recorded.
- Full job lifecycle (`queued → claimed → running → completed`) streamed live to the dashboard via SSE.
- The dashboard's "kill worker" control triggers a real process termination and a live, visible reclaim by another worker.

**Not yet verified:** dependency-*ordered* execution (a job provably waiting for its dependency to complete before running). Dependency validation at submission time works; enforced ordering during execution has not been tested end-to-end yet.

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

### 3. Run workers (separate terminals, as many as you want)
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
npm run dev   # http://localhost:5173, proxies /api and /events to :8000
```

### 5. Submit a job
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

## Architecture

- **Claiming** (`app/repositories/claim_query.sql`) — uses `FOR UPDATE SKIP LOCKED` so concurrent workers never claim the same row; contention is resolved at the database's row-locking level with no application-side mutex.
- **Lease / heartbeat** (`app/workers/worker.py`) — job execution races against a heartbeat task via `asyncio.wait(..., FIRST_COMPLETED)`. If the heartbeat fails to renew (ownership was already reclaimed elsewhere), the losing worker cancels its execution and does not touch the job's final state.
- **Dead-lettering** — a separate table rather than a status flag, keeping the primary claim query fast.
- **Dependencies** — a `NOT EXISTS` subquery against a `job_dependencies` edge table; validated at submission, not yet enforced during execution ordering.

## Tech stack

Python, FastAPI, SQLAlchemy (async), PostgreSQL (`LISTEN`/`NOTIFY`, row-level locking), React, Server-Sent Events.

## Known limitations

- The `/dev/kill-worker/{id}` endpoint sends a raw OS signal by PID — suitable for local demonstration, not a production termination pattern.
- Claiming uses interval-based polling rather than push notifications; `LISTEN`/`NOTIFY` is used specifically for the dashboard, where live updates matter most.
- No authentication on the API — out of scope for this project.
- Dependency-ordered execution is not yet implemented/verified.

## License

MIT
