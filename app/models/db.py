"""
Database models for the distributed job scheduler.

Design notes (why each field exists) are in comments next to the field —
read these, don't just skim past them. This is the part you need to be
able to defend in an interview.
"""

import uuid
import enum
from datetime import datetime

from sqlalchemy import (
    Column, String, Integer, DateTime, ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, ENUM
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class WorkerStatus(str, enum.Enum):
    ACTIVE = "active"
    DEAD = "dead"


class Worker(Base):
    """
    One row per worker PROCESS (not per machine — if you run 3 worker
    processes on one laptop, that's 3 rows). This exists so the dashboard
    can show a "fleet view" and so jobs.locked_by has something to point to.
    """
    __tablename__ = "workers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label = Column(String, nullable=False)  # human-readable, e.g. "worker-1"
    # OS process id. Only meaningful for local/dev demo purposes (e.g. the
    # dashboard's "kill worker" button) — a real deployment would run
    # workers as containers/pods and kill via the orchestrator, not a raw
    # pid, but for a single-machine student project this is the honest
    # equivalent.
    pid = Column(Integer, nullable=True)
    status = Column(
        ENUM(WorkerStatus, name="worker_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False, default=WorkerStatus.ACTIVE,
    )
    # Updated every time this worker renews a lease on any job. If this
    # goes stale beyond a threshold, the dashboard marks the worker "dead"
    # — separate from the per-job lease_expires_at, which is what actually
    # drives job reclaim logic.
    last_heartbeat = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    jobs = relationship("Job", back_populates="worker")


class Job(Base):
    """
    The core queue table. Every row is one unit of work.
    """
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # What kind of job this is — lets the worker loop dispatch to the
    # right handler function (e.g. "send_email", "resize_image").
    job_type = Column(String, nullable=False, index=True)

    # Arbitrary task-specific data. JSONB (not JSON) because Postgres
    # indexes and queries JSONB efficiently — you could later add a GIN
    # index here if you needed to query inside payloads.
    payload = Column(JSONB, nullable=False)

    status = Column(
        ENUM(JobStatus, name="job_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False, default=JobStatus.QUEUED, index=True,
    )

    # Higher number = claimed first. Plain int, no magic.
    priority = Column(Integer, nullable=False, default=0)

    # Prevents the SAME logical job being submitted twice (e.g. a client
    # retries an HTTP request and accidentally double-submits). Unique
    # constraint does the enforcement; the app decides what key to use.
    idempotency_key = Column(String, unique=True, nullable=True)

    # Delayed/scheduled jobs AND retry backoff both use this single field:
    # "don't claim this job until this timestamp." A fresh job defaults to
    # now(); a retried job gets run_after pushed forward.
    run_after = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)

    # --- Lease/lock fields: this trio IS the fault-tolerance mechanism ---
    # locked_by: which worker currently owns this job (NULL = unclaimed)
    locked_by = Column(UUID(as_uuid=True), ForeignKey("workers.id"), nullable=True)
    # locked_at: when it was first claimed (useful for debugging/metrics)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    # lease_expires_at: the worker must push this forward periodically
    # (heartbeat) while working. If now() > lease_expires_at and status is
    # still 'claimed'/'running', ANY worker may steal the job back — this
    # is what makes crash recovery automatic instead of requiring a
    # separate watchdog process.
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    worker = relationship("Worker", back_populates="jobs")

    __table_args__ = (
        # Composite index for the claim query: it filters on status,
        # orders by priority then created_at. Without this, claiming
        # under concurrent load degrades badly as the table grows.
        Index("ix_jobs_claim_query", "status", "priority", "created_at"),
    )


class JobDependency(Base):
    """
    Enables DAG-style execution: `job_id` cannot run until every row's
    `depends_on_job_id` for that job_id has status = completed.

    Composite PK on (job_id, depends_on_job_id) means a given dependency
    edge can only exist once — no accidental duplicate edges.
    """
    __tablename__ = "job_dependencies"

    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), primary_key=True)
    depends_on_job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), primary_key=True)


class DeadLetterJob(Base):
    """
    Where jobs go once attempts >= max_attempts. Kept as a SEPARATE table
    (not just a status flag on `jobs`) so that every query against the
    live queue — especially the claim query, which runs constantly — never
    has to scan or filter out dead rows. Keeps the hot path fast.
    """
    __tablename__ = "dead_letter_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    original_job_id = Column(UUID(as_uuid=True), nullable=False)
    job_type = Column(String, nullable=False)
    payload = Column(JSONB, nullable=False)
    failure_reason = Column(String, nullable=True)
    attempts_made = Column(Integer, nullable=False)
    moved_at = Column(DateTime(timezone=True), server_default=func.now())
