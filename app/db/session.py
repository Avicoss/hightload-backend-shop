from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    # 4 воркера × (90 + 10) = 400 соединений; postgres.max_connections=450 - запас есть
    pool_size=90,
    max_overflow=10,
    # Проверяет живость соединения перед выдачей из пула
    pool_pre_ping=True,
    # Не ждём 30 сек (default) в очереди на соединение - быстро отдаём 503 при пиковой нагрузке
    pool_timeout=5,
    # Пересоздаём соединения каждые 30 минут - защита от "stale connection" на фаерволах
    pool_recycle=1800,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    # Атрибуты объектов остаются доступны после коммита - нужно для чтения order.order_id
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency - открывает сессию и закрывает после запроса."""
    async with async_session_factory() as session:
        yield session
