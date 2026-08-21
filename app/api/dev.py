"""
DEV-ONLY endpoints. These exist purely to power the dashboard's live
"kill worker" demo button — they let a browser trigger a real SIGKILL
against a real local worker process, which is what makes that demo
honest instead of simulated.

This router should never be mounted in anything resembling a real
deployment: letting any caller kill arbitrary local processes by ID is
obviously unsafe outside a single-machine student demo. If you were
turning this into a real service, the equivalent capability would be
"cordon and terminate this pod" through your orchestrator's API, with
real auth in front of it — worth saying exactly that if an interviewer
asks why this endpoint exists.
"""

import os
import signal
import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.db.session import AsyncSessionLocal

router = APIRouter(prefix="/dev", tags=["dev-only"])


@router.post("/kill-worker/{worker_id}")
async def kill_worker(worker_id: uuid.UUID):
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            text("SELECT pid FROM workers WHERE id = :id"), {"id": worker_id}
        )).mappings().first()

    if row is None or row["pid"] is None:
        raise HTTPException(404, "worker not found or has no recorded pid")

    try:
        os.kill(row["pid"], signal.SIGKILL)
    except ProcessLookupError:
        raise HTTPException(410, "process already gone")

    return {"killed_pid": row["pid"], "worker_id": str(worker_id)}
