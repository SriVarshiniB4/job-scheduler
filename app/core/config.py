"""
App configuration, loaded from environment variables.

Why this exists as its own file: hardcoding a database URL in your code
is a mistake you only make once (you already had a leaked API key on the
RAG project — same category of problem). Keeping config centralized and
env-based means secrets never get committed to git.
"""

import os


class Settings:
    # Example: postgresql+asyncpg://user:password@localhost:5432/jobscheduler
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/jobscheduler",
    )

    # How long a claimed job's lease lasts before another worker may
    # steal it back, assuming the owning worker stops renewing.
    LEASE_DURATION_SECONDS: int = int(os.environ.get("LEASE_DURATION_SECONDS", 15))

    # How often a worker renews its lease on the job it's running.
    # Should be comfortably shorter than LEASE_DURATION_SECONDS —
    # e.g. renew every 5s on a 15s lease, so a single missed renewal
    # doesn't immediately cause a false reclaim.
    HEARTBEAT_INTERVAL_SECONDS: int = int(
        os.environ.get("HEARTBEAT_INTERVAL_SECONDS", 5)
    )


settings = Settings()
