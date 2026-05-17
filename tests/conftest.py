"""
Фикстуры для интеграционных тестов

Ключевые решения:
- NullPool: каждое обращение к БД создаёт свежее соединение без привязки к event loop
- Все сессии закрываются явно через await session.close() - это критично
  AsyncSession.__aexit__ использует asyncio.create_task(self.close()), что создаёт
  фоновую задачу вне event loop теста и вызывает "Future attached to a different loop"
  Явный await session.close() в finally-блоке гарантирует корректное закрытие
- client создаёт НОВУЮ сессию на каждый запрос → реальная конкурентность в тестах
- db_session - отдельная сессия только для верификации состояния после запросов
- clean_tables - удаляет всё ДО каждого теста
- Синглтон Redis сбрасывается после каждого теста с client-фикстурой,
  чтобы lifespan следующего теста создал свежее соединение
"""
import asyncio
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cache import redis as _redis_cache_module
from app.core.config import settings
from app.db.models import Base, Product, User, UserStatus
from app.db.session import get_session
from app.main import app

# Отдельная тестовая БД - не трогаем продакшн данные
_TEST_DB_URL = settings.database_url.replace(
    f"/{settings.POSTGRES_DB}", f"/{settings.POSTGRES_DB}_test"
)

# NullPool: соединения не кешируются - нет привязки к конкретному event loop
_test_engine = create_async_engine(_TEST_DB_URL, poolclass=NullPool)
_test_session_factory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest.fixture(scope="session")
def event_loop():
    """Единый event loop на всю сессию - фикстуры и тесты используют один loop
    asyncio_default_fixture_loop_scope=session задаёт loop для фикстур,
    а этот фикстуром задаёт тот же loop для тест-функций
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_db():
    """Пересоздаём схему один раз на всю сессию тестов
    drop_all перед create_all гарантирует, что тест-БД всегда соответствует текущим моделям
    (create_all без drop_all не изменяет уже существующие таблицы и constraint-ы)
    """
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await _test_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_tables():
    """Чистим таблицы и Redis ДО каждого теста - каждый тест начинает с чистого состояния."""
    session = _test_session_factory()
    try:
        async with session.begin():
            for table in reversed(Base.metadata.sorted_tables):
                await session.execute(table.delete())
    finally:
        await session.close()
    # Сбрасываем Redis: ключи идемпотентности из предыдущих запусков не должны влиять
    redis = _redis_cache_module.get_redis()
    await redis.flushdb()


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Сессия для верификации состояния БД после запросов."""
    session = _test_session_factory()
    try:
        yield session
    finally:
        # Явный rollback + close вместо async with → нет create_task в __aexit__
        await session.rollback()
        await session.close()


@pytest_asyncio.fixture
async def active_user() -> User:
    """Активный пользователь - коммитит в собственной сессии, виден всем."""
    session = _test_session_factory()
    try:
        user = User(user_id=1, email="test@example.com", status=UserStatus.active)
        session.add(user)
        await session.commit()
        return user
    finally:
        await session.close()


@pytest_asyncio.fixture
async def banned_user() -> User:
    session = _test_session_factory()
    try:
        user = User(user_id=2, email="banned@example.com", status=UserStatus.banned)
        session.add(user)
        await session.commit()
        return user
    finally:
        await session.close()


@pytest_asyncio.fixture
async def product_with_stock() -> Product:
    session = _test_session_factory()
    try:
        product = Product(
            product_id=1,
            stock=100,
            description="Тестовый товар",
        )
        session.add(product)
        await session.commit()
        return product
    finally:
        await session.close()


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    """
    HTTP-клиент с тестовой БД
    Каждый запрос получает СВОЮ сессию из пула - это позволяет
    тестировать настоящую конкурентность (asyncio.gather с 10 запросами)
    """
    async def test_get_session():
        # async with (не явный close) - обязательно для FastAPI-dependency контекста:
        # __aexit__ использует create_task, которая работает корректно в этом контексте
        async with _test_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = test_get_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def make_jwt(user_id: int, expired: bool = False) -> str:
    """Генерирует JWT для тестов."""
    delta = timedelta(hours=-1) if expired else timedelta(hours=1)
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(tz=timezone.utc) + delta,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
