"""
Тесты идемпотентности POST /api/v1/purchase
"""
import asyncio

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OutboxEvent, Product
from tests.conftest import make_jwt


async def test_idempotent_second_request(
    client: AsyncClient,
    active_user,
    product_with_stock,
    db_session: AsyncSession,
):
    """Второй запрос с тем же ключом возвращает 200 и не создаёт дубль заказа."""
    headers = {
        "Authorization": f"Bearer {make_jwt(active_user.user_id)}",
        "Idempotency-Key": "idem-key-001",
    }
    body = {"product_id": product_with_stock.product_id, "purchased_count": 5}

    r1 = await client.post("/api/v1/purchase", json=body, headers=headers)
    r2 = await client.post("/api/v1/purchase", json=body, headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 200

    count = await db_session.execute(
        select(func.count()).select_from(Order).where(
            Order.idempotency_key == "idem-key-001"
        )
    )
    assert count.scalar() == 1


async def test_stock_deducted_only_once(
    client: AsyncClient,
    active_user,
    product_with_stock,
    db_session: AsyncSession,
):
    """Stock уменьшился ровно один раз, несмотря на два одинаковых запроса."""
    headers = {
        "Authorization": f"Bearer {make_jwt(active_user.user_id)}",
        "Idempotency-Key": "idem-key-002",
    }
    body = {"product_id": product_with_stock.product_id, "purchased_count": 10}

    await client.post("/api/v1/purchase", json=body, headers=headers)
    await client.post("/api/v1/purchase", json=body, headers=headers)

    result = await db_session.execute(
        select(Product).where(Product.product_id == product_with_stock.product_id)
    )
    assert result.scalar_one().stock == 90


async def test_outbox_event_created_only_once(
    client: AsyncClient,
    active_user,
    product_with_stock,
    db_session: AsyncSession,
):
    """Outbox содержит ровно одно событие, даже при повторном запросе."""
    headers = {
        "Authorization": f"Bearer {make_jwt(active_user.user_id)}",
        "Idempotency-Key": "idem-key-003",
    }
    body = {"product_id": product_with_stock.product_id, "purchased_count": 1}

    await client.post("/api/v1/purchase", json=body, headers=headers)
    await client.post("/api/v1/purchase", json=body, headers=headers)

    count = await db_session.execute(
        select(func.count()).select_from(OutboxEvent)
    )
    assert count.scalar() == 1


async def test_parallel_requests_with_same_key_no_duplicate(
    client: AsyncClient,
    active_user,
    product_with_stock,
    db_session: AsyncSession,
):
    """
    10 параллельных запросов с одним Idempotency-Key - ровно один заказ
    Race condition обрабатывается через UNIQUE constraint + IntegrityError catch
    """
    headers = {
        "Authorization": f"Bearer {make_jwt(active_user.user_id)}",
        "Idempotency-Key": "idem-key-race-001",
    }
    body = {"product_id": product_with_stock.product_id, "purchased_count": 1}

    responses = await asyncio.gather(
        *[client.post("/api/v1/purchase", json=body, headers=headers) for _ in range(10)]
    )

    # Все 10 запросов должны вернуть 200 (идемпотентность)
    assert all(r.status_code == 200 for r in responses)

    # В БД - ровно один заказ
    count = await db_session.execute(
        select(func.count()).select_from(Order).where(
            Order.idempotency_key == "idem-key-race-001"
        )
    )
    assert count.scalar() == 1

    # Stock уменьшился ровно на 1
    result = await db_session.execute(
        select(Product).where(Product.product_id == product_with_stock.product_id)
    )
    assert result.scalar_one().stock == 99
