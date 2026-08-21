"""
One-off script: creates all tables from app/models/db.py against
whatever DATABASE_URL is configured. Run manually during dev/testing —
in a real deployment you'd use Alembic migrations instead, but for
getting the schema onto a fresh DB right now, this is enough.
"""

import asyncio
from sqlalchemy import text
from app.db.session import engine
from app.models.db import Base


async def create_all():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Trigger: fire pg_notify('job_events', ...) any time a job row's
        # status changes. This is what lets the SSE endpoint push events
        # to the browser instantly instead of polling the jobs table.
        # We only fire on UPDATE-of-status (not every column change) to
        # avoid spamming events for e.g. lease renewals, which happen
        # every few seconds per running job and nobody needs to see live.
        await conn.execute(text("""
            CREATE OR REPLACE FUNCTION notify_job_status_change() RETURNS trigger AS $$
            BEGIN
                IF (TG_OP = 'INSERT') OR (OLD.status IS DISTINCT FROM NEW.status) THEN
                    PERFORM pg_notify(
                        'job_events',
                        json_build_object(
                            'job_id', NEW.id,
                            'job_type', NEW.job_type,
                            'status', NEW.status,
                            'priority', NEW.priority,
                            'attempts', NEW.attempts,
                            'worker_id', NEW.locked_by,
                            'at', now()
                        )::text
                    );
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """))
        await conn.execute(text("""
            DROP TRIGGER IF EXISTS job_status_change_trigger ON jobs;
        """))
        await conn.execute(text("""
            CREATE TRIGGER job_status_change_trigger
            AFTER INSERT OR UPDATE ON jobs
            FOR EACH ROW EXECUTE FUNCTION notify_job_status_change();
        """))

    print("All tables created, notify trigger installed.")


if __name__ == "__main__":
    asyncio.run(create_all())
