"""
Live event stream (SSE) for job status changes.

Why a SEPARATE raw asyncpg connection instead of reusing the SQLAlchemy
pool: LISTEN/NOTIFY requires a connection to stay open indefinitely,
dedicated to listening — you can't share it with the connection pool
that's also running normal queries, since pooled connections get
recycled and normal queries would conflict with a long-held listener.
One dedicated connection per SSE client is the correct pattern here.
"""

import asyncio
import json
import asyncpg
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.config import settings

router = APIRouter(tags=["events"])


async def _event_generator(request: Request):
    # asyncpg wants a plain postgresql:// URL, not the +asyncpg SQLAlchemy variant
    raw_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url)

    queue: asyncio.Queue = asyncio.Queue()

    def _on_notify(connection, pid, channel, payload):
        queue.put_nowait(payload)

    await conn.add_listener("job_events", _on_notify)

    try:
        # Send an initial comment so the browser's EventSource connection
        # opens immediately rather than appearing to hang.
        yield ": connected\n\n"

        while True:
            if await request.is_disconnected():
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=15)
                yield f"data: {payload}\n\n"
            except asyncio.TimeoutError:
                # Heartbeat comment line — keeps intermediary proxies/browsers
                # from timing out an idle SSE connection.
                yield ": keep-alive\n\n"
    finally:
        await conn.remove_listener("job_events", _on_notify)
        await conn.close()


@router.get("/events")
async def job_events(request: Request):
    return StreamingResponse(
        _event_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering if ever deployed behind it
        },
    )
