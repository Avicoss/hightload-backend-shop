"""
Начальное заполнение БД - запускать один раз после `alembic upgrade head`

Создаёт тестовые данные, достаточные для демонстрации эндпоинта из задания:
    POST /api/v1/purchase
    Body: {"user_id": 12345, "product_id": 42, "purchased_count": 2}

Запуск:
    docker-compose exec api python scripts/seed.py

Скрипт идемпотентен - повторный запуск безопасен
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.db.models import Product, User, UserStatus

USERS = [
    {"user_id": 12345, "email": "demo@example.com",   "status": UserStatus.active},
    {"user_id": 1,     "email": "user1@example.com",  "status": UserStatus.active},
    {"user_id": 2,     "email": "user2@example.com",  "status": UserStatus.active},
]

PRODUCTS = [
    {"product_id": 42, "description": "Demo Product", "stock": 1_000_000},
    {"product_id": 1,  "description": "Laptop Pro",   "stock": 1_000_000},
    {"product_id": 2,  "description": "Desktop Pro",  "stock": 1_000_000},
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
            for u in USERS:
                await session.execute(
                    pg_insert(User)
                    .values(**u)
                    .on_conflict_do_update(
                        index_elements=["user_id"],
                        set_={"status": u["status"]},
                    )
                )

            for p in PRODUCTS:
                await session.execute(
                    pg_insert(Product)
                    .values(**p)
                    .on_conflict_do_update(
                        index_elements=["product_id"],
                        set_={"stock": p["stock"], "description": p["description"]},
                    )
                )

    await engine.dispose()

    print("Seed выполнен:")
    for u in USERS:
        print(f"  user_id={u['user_id']}  email={u['email']}  status={u['status'].value}")
    for p in PRODUCTS:
        print(f"  product_id={p['product_id']}  description={p['description']}  stock={p['stock']:,}")

    print()
    print("Пример запроса (без JWT):")
    print('  curl -X POST http://localhost:8000/api/v1/purchase \\')
    print('       -H "Content-Type: application/json" \\')
    print('       -d \'{"user_id": 12345, "product_id": 42, "purchased_count": 2}\'')

    print()
    print(f"JWT для user_id=12345 (24 часа):")
    print(f"  {_make_jwt(12345)}")


if __name__ == "__main__":
    asyncio.run(seed())
