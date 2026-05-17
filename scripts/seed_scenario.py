"""
Подготовка сценарного теста: 1000 пользователей, 2 товара по 1M остатка

Запуск:
    python scripts/seed_scenario.py

Вывод:
    export SCENARIO_JWT=<token>
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.db.models import Product, User, UserStatus

TEST_USER_ID = 1
INITIAL_STOCK = 10_000_000

PRODUCTS = [
    {"product_id": 1, "description": "laptop",  "stock": INITIAL_STOCK},
    {"product_id": 2, "description": "desktop", "stock": INITIAL_STOCK},
]


def _make_jwt(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(tz=timezone.utc) + timedelta(hours=24),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


async def seed() -> None:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        async with session.begin():
            await session.execute(text("TRUNCATE orders, outbox_events, products, users RESTART IDENTITY CASCADE"))

            await session.execute(
                pg_insert(User).values(
                    user_id=TEST_USER_ID,
                    email="scenario@example.com",
                    status=UserStatus.active,
                )
            )

            for p in PRODUCTS:
                await session.execute(pg_insert(Product).values(**p))

    await engine.dispose()
    print(f"Сброшено и засеяно: laptop(id=1) и desktop(id=2), остаток по {INITIAL_STOCK:,} шт.")
    print(f"export SCENARIO_JWT={_make_jwt(TEST_USER_ID)}")


if __name__ == "__main__":
    asyncio.run(seed())
