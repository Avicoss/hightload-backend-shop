"""
Интеграционные тесты GET /api/v1/products/{product_id}

Проверяем:
- корректный ответ при cache miss (чтение из БД)
- данные кэшируются в Redis после первого запроса
- stock НЕ попадает в кэш и ответ
- повторный запрос обслуживается из Redis (не из БД)
- 404 при отсутствии товара
"""
import json

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import _product_key, get_redis
from app.db.models import Product


async def test_get_product_returns_data(client: AsyncClient, product_with_stock: Product):
    r = await client.get(f"/api/v1/products/{product_with_stock.product_id}")

    assert r.status_code == 200
    body = r.json()
    assert body["product_id"] == product_with_stock.product_id
    assert body["description"] == product_with_stock.description


async def test_stock_not_in_response(client: AsyncClient, product_with_stock: Product):
    """Stock намеренно исключён из карточки товара."""
    r = await client.get(f"/api/v1/products/{product_with_stock.product_id}")

    assert "stock" not in r.json()


async def test_product_cached_after_first_request(
    client: AsyncClient, product_with_stock: Product
):
    """После первого запроса данные должны появиться в Redis."""
    await client.get(f"/api/v1/products/{product_with_stock.product_id}")

    raw = await get_redis().get(_product_key(product_with_stock.product_id))
    assert raw is not None

    cached = json.loads(raw)
    assert cached["product_id"] == product_with_stock.product_id
    # Stock не должен быть в кэше
    assert "stock" not in cached


async def test_second_request_served_from_cache(
    client: AsyncClient,
    product_with_stock: Product,
    db_session: AsyncSession,
):
    """Повторный запрос возвращает данные из Redis, игнорируя изменения в БД."""
    r1 = await client.get(f"/api/v1/products/{product_with_stock.product_id}")
    original_description = r1.json()["description"]

    # Меняем описание в БД напрямую
    await db_session.execute(
        update(Product)
        .where(Product.product_id == product_with_stock.product_id)
        .values(description="Изменённое описание")
    )
    await db_session.commit()

    # Второй запрос должен вернуть закэшированное старое описание
    r2 = await client.get(f"/api/v1/products/{product_with_stock.product_id}")
    assert r2.json()["description"] == original_description


async def test_product_not_found_returns_404(client: AsyncClient):
    r = await client.get("/api/v1/products/99999")

    assert r.status_code == 404
    assert r.json()["code"] == "PRODUCT_NOT_FOUND"
