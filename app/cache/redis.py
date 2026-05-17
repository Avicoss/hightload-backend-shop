import json
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings

# TTL кэша карточки товара - 5 минут
PRODUCT_CACHE_TTL = 300

# TTL ключа идемпотентности - 24 часа
IDEMPOTENCY_TTL = 86400

_redis_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Синглтон клиента редис"""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


def _product_key(product_id: int) -> str:
    return f"product:{product_id}"


def _idem_key(idempotency_key: str) -> str:
    return f"idem:{idempotency_key}"


async def get_cached_product(product_id: int) -> dict[str, Any] | None:
    """Возвращает карточку товара из кэша. Запас намеренно исключён"""
    data = await get_redis().get(_product_key(product_id))
    return json.loads(data) if data else None


async def set_cached_product(product_id: int, product_data: dict[str, Any]) -> None:
    """Кэширует карточку товара без поля stock - stock всегда из БД"""
    safe_data = {k: v for k, v in product_data.items() if k != "stock"}
    await get_redis().set(
        _product_key(product_id),
        json.dumps(safe_data, default=str),
        ex=PRODUCT_CACHE_TTL,
    )


async def invalidate_product_cache(product_id: int) -> None:
    """Инвалидирует кэш карточки товара при изменении данных продукта"""
    await get_redis().delete(_product_key(product_id))


async def check_idempotency(idempotency_key: str) -> str | None:
    """Быстрая проверка идемпотентности через редис. Возвращает order_id или None"""
    return await get_redis().get(_idem_key(idempotency_key))


async def set_idempotency(idempotency_key: str, order_id: int) -> None:
    """Сохраняет ключ идемпотентности с TTL 24 часа."""
    await get_redis().set(_idem_key(idempotency_key), str(order_id), ex=IDEMPOTENCY_TTL)
