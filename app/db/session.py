"""
Database engine + session setup.

We use SQLAlchemy's ASYNC engine (not the sync one) because FastAPI is
async, and because our worker loop needs to run many concurrent DB
operations (multiple workers polling at once) without blocking threads.
"""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,       # set True temporarily if you want to see every SQL statement
    pool_size=20,      # generous pool since we'll run many worker processes/tasks
    max_overflow=10,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # so objects stay usable after commit without a re-fetch
)


async def get_session() -> AsyncSession:
    """FastAPI dependency / general-purpose session getter."""
    async with AsyncSessionLocal() as session:
        yield session
