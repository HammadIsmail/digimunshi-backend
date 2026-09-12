from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.core.config import get_settings
from fastapi import HTTPException

_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.DATABASE_URL
        if "?" in url:
            url = url.split("?")[0]
        _engine = create_async_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=300,
            connect_args={"ssl": "require"}
        )

    return _engine


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)
    return _session_factory


async def get_db():
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except HTTPException:
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise
