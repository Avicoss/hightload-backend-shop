import logging

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import check_idempotency, set_idempotency
from app.core.exceptions import (
    InsufficientStockError,
    ProductNotFoundError,
    UserNotActiveError,
)
from app.db.models import Order, OutboxEvent, OutboxStatus, Product, User, UserStatus

logger = logging.getLogger(__name__)


async def process_purchase(
    session: AsyncSession,
    user_id: int,
    product_id: int,
    purchased_count: int,
    idempotency_key: str,
) -> dict:
    """
    Атомарно выполняет покупку:
    1. Redis fast path - мгновенный возврат при повторном запросе
    2. Валидация пользователя
    3. Атомарный UPDATE stock (без явных локов, один SQL)
    4. INSERT order + outbox_event в одной транзакции
    5. Обновляет Redis-кэш идемпотентности ПОСЛЕ коммита (вне транзакции)
    """
    try:
        cached_order_id = await check_idempotency(idempotency_key)
        if cached_order_id:
            logger.debug("Idempotency hit (Redis): key=%s", idempotency_key)
            return {"status": "success"}
    except Exception:
        # Redis недоступен - продолжаем по медленному пути через БД
        logger.warning("Redis недоступен, пропускаем fast-path: key=%s", idempotency_key)

    order: Order | None = None
    # order_id найденного дубля - Redis обновляется после транзакции, не внутри
    recovered_order_id: int | None = None

    try:
        async with session.begin():
            user = await session.get(User, user_id)
            if user is None or user.status != UserStatus.active:
                raise UserNotActiveError(user_id)

            # Slow path: Redis мог пропустить запись (рестарт, eviction)
            existing = await session.execute(
                select(Order).where(Order.idempotency_key == idempotency_key)
            )
            existing_order = existing.scalar_one_or_none()
            if existing_order:
                logger.debug(
                    "Idempotency hit (DB): key=%s order=%d",
                    idempotency_key,
                    existing_order.order_id,
                )
                # Не вызываем Redis внутри открытой транзакции - фиксируем ID и выходим
                recovered_order_id = existing_order.order_id
            else:
                # Атомарный UPDATE: одна операция без SELECT + проверка stock >= count
                result = await session.execute(
                    text(
                        """
                        UPDATE products
                           SET stock = stock - :count
                         WHERE product_id = :product_id
                           AND stock >= :count
                        RETURNING 1
                        """
                    ),
                    {"count": purchased_count, "product_id": product_id},
                )
                if result.fetchone() is None:
                    # UPDATE вернул пустой результат - нет товара или недостаточно остатков
                    product_row = await session.execute(
                        select(Product.product_id, Product.stock).where(
                            Product.product_id == product_id
                        )
                    )
                    product_data = product_row.first()
                    if product_data is None:
                        raise ProductNotFoundError(product_id)
                    raise InsufficientStockError(
                        available=product_data.stock,
                        requested=purchased_count,
                    )

                order = Order(
                    user_id=user_id,
                    product_id=product_id,
                    purchased_count=purchased_count,
                    idempotency_key=idempotency_key,
                )
                session.add(order)
                # flush нужен для получения order_id до коммита
                await session.flush()

                payload = {
                    "order_id": order.order_id,
                    "user_id": user_id,
                    "product_id": product_id,
                    "purchased_count": purchased_count,
                    "currency": "UAH",
                }

                outbox_event = OutboxEvent(
                    event_type="order.created",
                    payload=payload,
                    status=OutboxStatus.pending,
                )
                session.add(outbox_event)

    except IntegrityError as exc:
        # Ловим только уникальность idempotency_key - проверяем DETAIL ошибки, а не имя constraint
        # Имена constraint отличаются между create_all (auto-name) и migration (explicit name),
        # но DETAIL всегда содержит имя колонки
        if "idempotency_key" not in str(exc.orig):
            raise
        logger.warning(
            "Idempotency race condition resolved: key=%s user=%d product=%d",
            idempotency_key,
            user_id,
            product_id,
        )
        return {"status": "success"}

    # Redis-вызовы выполняются ПОСЛЕ закрытия транзакции
    # Сбой Redis не должен откатывать уже совершённую покупку
    if recovered_order_id is not None:
        try:
            await set_idempotency(idempotency_key, recovered_order_id)
        except Exception:
            logger.warning("Redis недоступен, ключ идемпотентности не закэширован: key=%s", idempotency_key)
        return {"status": "success"}

    if order is not None:
        try:
            await set_idempotency(idempotency_key, order.order_id)
        except Exception:
            logger.warning(
                "Redis недоступен после покупки: order=%d key=%s",
                order.order_id,
                idempotency_key,
            )
        logger.info(
            "Purchase completed: order=%d user=%d product=%d count=%d",
            order.order_id,
            user_id,
            product_id,
            purchased_count,
            extra={
                "order_id": order.order_id,
                "user_id": user_id,
                "product_id": product_id,
                "purchased_count": purchased_count,
            },
        )

    return {"status": "success"}
