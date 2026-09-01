"""Database session and engine setup for the request-tracking metadata store."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url_async,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    pool_pre_ping=True,
)

async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base class for the service's SQLAlchemy metadata models."""


async def init_db() -> None:
    """Create database tables if they do not already exist.

    Notes
    -----
    This project keeps schema management intentionally simple for now. For a
    growing application, replace this with Alembic migrations.

    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield a database session for dependency injection.

    Yields
    ------
    AsyncSession
        An SQLAlchemy async session scoped to the request lifecycle.

    """
    async with async_session_maker() as session:
        yield session


@asynccontextmanager
async def db_session() -> AsyncIterator[AsyncSession]:
    """Yield a database session within an async context manager.

    Yields
    ------
    AsyncSession
        A transactional database session for ad hoc work.

    """
    async with async_session_maker() as session:
        yield session


async def db_healthy() -> bool:
    """Check whether the metadata database is reachable.

    Returns
    -------
    bool
        ``True`` when the database responds successfully to a lightweight probe.

    """
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
