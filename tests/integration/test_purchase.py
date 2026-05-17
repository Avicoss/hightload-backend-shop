"""
Интеграционные тесты endpoint POST /api/v1/purchase

client и db_session - независимые сессии:
- client создаёт новую сессию на каждый запрос (реальная конкурентность)
- db_session используется только для верификации состояния после запросов
"""
import asyncio

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OutboxEvent, OutboxStatus, Product
from tests.conftest import make_jwt


async def test_successful_purchase(client: AsyncClient, active_user, product_with_stock):
    response = await client.post(
        "/api/v1/purchase",
        json={"product_id": product_with_stock.product_id, "purchased_count": 3},
        headers={
            "Authorization": f"Bearer {make_jwt(active_user.user_id)}",
            "Idempotency-Key": "key-success-001",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"status": "success"}


async def test_stock_decremented_after_purchase(
    client: AsyncClient,
    active_user,
    product_with_stock,
    db_session: AsyncSession,
):
    """После покупки stock уменьшился ровно на purchased_count."""
    await client.post(
        "/api/v1/purchase",
        json={"product_id": product_with_stock.product_id, "purchased_count": 10},
        headers={
            "Authorization": f"Bearer {make_jwt(active_user.user_id)}",
            "Idempotency-Key": "key-stock-001",
        },
    )
    result = await db_session.execute(
        select(Product).where(Product.product_id == product_with_stock.product_id)
    )
    assert result.scalar_one().stock == 90


async def test_outbox_event_created(
    client: AsyncClient,
    active_user,
    product_with_stock,
    db_session: AsyncSession,
):
    """Вместе с заказом создаётся outbox-событие в статусе pending или sent."""
    await client.post(
        "/api/v1/purchase",
        json={"product_id": product_with_stock.product_id, "purchased_count": 1},
        headers={
            "Authorization": f"Bearer {make_jwt(active_user.user_id)}",
            "Idempotency-Key": "key-outbox-001",
        },
    )
    result = await db_session.execute(select(OutboxEvent))
    event = result.scalar_one()
    assert event.event_type == "order.created"
    assert event.payload["currency"] == "UAH"


async def test_insufficient_stock_returns_409(
    client: AsyncClient,
    active_user,
    product_with_stock,
):
    response = await client.post(
        "/api/v1/purchase",
        json={"product_id": product_with_stock.product_id, "purchased_count": 999},
        headers={
            "Authorization": f"Bearer {make_jwt(active_user.user_id)}",
            "Idempotency-Key": "key-insuf-001",
        },
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "INSUFFICIENT_STOCK"
    assert body["available_stock"] == 100
    assert body["requested_count"] == 999
    assert "title" in body
    assert "detail" in body


async def test_product_not_found_returns_404(client: AsyncClient, active_user):
    response = await client.post(
        "/api/v1/purchase",
        json={"product_id": 99999, "purchased_count": 1},
        headers={
            "Authorization": f"Bearer {make_jwt(active_user.user_id)}",
            "Idempotency-Key": "key-notfound-001",
        },
    )
    assert response.status_code == 404
    assert response.json()["code"] == "PRODUCT_NOT_FOUND"


async def test_banned_user_returns_403(
    client: AsyncClient,
    banned_user,
    product_with_stock,
):
    response = await client.post(
        "/api/v1/purchase",
        json={"product_id": product_with_stock.product_id, "purchased_count": 1},
        headers={
            "Authorization": f"Bearer {make_jwt(banned_user.user_id)}",
            "Idempotency-Key": "key-banned-001",
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "USER_NOT_ACTIVE"


async def test_invalid_token_returns_401(client: AsyncClient, product_with_stock):
    response = await client.post(
        "/api/v1/purchase",
        json={"product_id": product_with_stock.product_id, "purchased_count": 1},
        headers={
            "Authorization": "Bearer invalid.token.here",
            "Idempotency-Key": "key-auth-001",
        },
    )
    assert response.status_code == 401


async def test_expired_token_returns_401(
    client: AsyncClient,
    active_user,
    product_with_stock,
):
    response = await client.post(
        "/api/v1/purchase",
        json={"product_id": product_with_stock.product_id, "purchased_count": 1},
        headers={
            "Authorization": f"Bearer {make_jwt(active_user.user_id, expired=True)}",
            "Idempotency-Key": "key-expired-001",
        },
    )
    assert response.status_code == 401


async def test_stock_never_goes_negative(
    client: AsyncClient,
    active_user,
    product_with_stock,
    db_session: AsyncSession,
):
    """
    10 параллельных запросов по 20 штук при stock=100
    Ровно 5 должны успешно купить, остальные получить 409
    Stock в конце - ровно 0, не отрицательный
    """
    token = make_jwt(active_user.user_id)

    async def buy(idx: int):
        return await client.post(
            "/api/v1/purchase",
            json={"product_id": product_with_stock.product_id, "purchased_count": 20},
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": f"concurrent-{idx}",
            },
        )

    responses = await asyncio.gather(*[buy(i) for i in range(10)])

    success = sum(1 for r in responses if r.status_code == 200)
    conflict = sum(1 for r in responses if r.status_code == 409)

    assert success == 5, f"Ожидали 5 успешных, получили {success}"
    assert conflict == 5, f"Ожидали 5 отказов, получили {conflict}"

    result = await db_session.execute(
        select(Product).where(Product.product_id == product_with_stock.product_id)
    )
    assert result.scalar_one().stock == 0
