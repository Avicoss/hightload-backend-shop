"""
Outbox Worker - читает pending-события из outbox_events и публикует в RabbitMQ

Гарантии доставки:
- at-least-once: событие публикуется до обновления статуса в БД
- при краше воркера зависшие processing-события сбрасываются в pending

Retry-стратегия:
- экспоненциальный backoff с jitter: delay = min(2^n, 300) + random(0, 1) секунды
- после OUTBOX_MAX_RETRIES попыток - статус 'dlq'
"""
import asyncio
import json
import logging
import random
import signal
import time
from datetime import datetime, timedelta, timezone

import aio_pika
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import OutboxEvent, OutboxStatus

logger = logging.getLogger(__name__)

EXCHANGE_NAME = "orders"
QUEUE_NAME = "orders.created"
DLQ_EXCHANGE_NAME = "orders.dlq"
DLQ_QUEUE_NAME = "orders.created.dlq"


def _calc_next_retry_at(retry_count: int) -> datetime:
    """Экспоненциальный backoff с jitter. Cap - 5 минут."""
    delay = min(2 ** retry_count, 300) + random.uniform(0, 1)
    return datetime.now(tz=timezone.utc) + timedelta(seconds=delay)


async def setup_rabbitmq(channel: aio_pika.Channel) -> aio_pika.Exchange:
    """
    Объявляет Quorum Queues + exchanges
    Idempotent - безопасно вызывать при каждом старте и при переподключении
    """
    dlq_exchange = await channel.declare_exchange(
        DLQ_EXCHANGE_NAME,
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )
    dlq_queue = await channel.declare_queue(
        DLQ_QUEUE_NAME,
        durable=True,
        arguments={"x-queue-type": "quorum"},
    )
    await dlq_queue.bind(dlq_exchange, routing_key=DLQ_QUEUE_NAME)

    exchange = await channel.declare_exchange(
        EXCHANGE_NAME,
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )
    queue = await channel.declare_queue(
        QUEUE_NAME,
        durable=True,
        arguments={
            "x-queue-type": "quorum",
            "x-dead-letter-exchange": DLQ_EXCHANGE_NAME,
            "x-dead-letter-routing-key": DLQ_QUEUE_NAME,
        },
    )
    await queue.bind(exchange, routing_key=QUEUE_NAME)

    return exchange


async def recover_stuck_events(session: AsyncSession, threshold_minutes: int) -> None:
    """
    Сбрасывает события, застрявшие в 'processing' дольше threshold_minutes
    Защита от краша воркера между SELECT и публикацией
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=threshold_minutes)
    async with session.begin():
        result = await session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.status == OutboxStatus.processing,
                OutboxEvent.updated_at < cutoff,
            )
            .values(status=OutboxStatus.pending, next_retry_at=datetime.now(tz=timezone.utc))
            .returning(OutboxEvent.id)
        )
        recovered = result.fetchall()
    if recovered:
        logger.warning("Сброшено %d зависших событий из processing в pending", len(recovered))


async def process_batch(
    session: AsyncSession,
    exchange: aio_pika.Exchange,
    max_retries: int,
    batch_size: int,
) -> int:
    """
    Обрабатывает один батч pending-событий

    Порядок операций:
    1. SELECT FOR UPDATE SKIP LOCKED - берём батч, не мешая другим воркерам
    2. Помечаем как processing (короткая транзакция)
    3. Публикуем в RabbitMQ за пределами транзакции
    4. Обновляем статус по результату публикации

    Возвращает количество обработанных событий
    """
    async with session.begin():
        result = await session.execute(
            select(OutboxEvent)
            .where(
                OutboxEvent.status == OutboxStatus.pending,
                OutboxEvent.next_retry_at <= datetime.now(tz=timezone.utc),
            )
            .order_by(OutboxEvent.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        events = result.scalars().all()

        if not events:
            return 0

        event_ids = [e.id for e in events]
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id.in_(event_ids))
            .values(status=OutboxStatus.processing)
        )

    for event in events:
        await _publish_event(session, exchange, event, max_retries)

    return len(events)


async def _publish_event(
    session: AsyncSession,
    exchange: aio_pika.Exchange,
    event: OutboxEvent,
    max_retries: int,
) -> None:
    """Публикует одно событие. При ошибке - обновляет retry_count или переводит в dlq."""
    try:
        message = aio_pika.Message(
            body=json.dumps(event.payload).encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
            type=event.event_type,
        )
        await exchange.publish(message, routing_key=QUEUE_NAME)

        async with session.begin():
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == event.id)
                .values(
                    status=OutboxStatus.sent,
                    sent_at=datetime.now(tz=timezone.utc),
                    error=None,
                )
            )

    except Exception as exc:
        logger.error("Ошибка публикации события id=%d: %s", event.id, exc)
        new_retry_count = event.retry_count + 1

        if new_retry_count >= max_retries:
            new_status = OutboxStatus.dlq
            next_retry_at = None
            logger.error("Событие id=%d переведено в DLQ после %d попыток", event.id, new_retry_count)
        else:
            new_status = OutboxStatus.pending
            next_retry_at = _calc_next_retry_at(new_retry_count)

        async with session.begin():
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == event.id)
                .values(
                    status=new_status,
                    retry_count=new_retry_count,
                    next_retry_at=next_retry_at,
                    error=str(exc)[:1000],
                )
            )


async def _wait_or_stop(shutdown: asyncio.Event, timeout: float) -> None:
    """Ждёт timeout секунд или сигнала завершения - выходит немедленно при shutdown."""
    try:
        await asyncio.wait_for(shutdown.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass


async def run_worker() -> None:
    """Главный цикл outbox-воркера."""
    shutdown_event = asyncio.Event()

    engine = create_async_engine(
        settings.database_url,
        pool_size=5,
        pool_pre_ping=True,
        pool_timeout=5,
        pool_recycle=1800,
    )
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=settings.OUTBOX_BATCH_SIZE)
    exchange = await setup_rabbitmq(channel)

    # loop.add_signal_handler корректно интегрирован с event loop - в отличие от signal.signal
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown_event.set)

    logger.info(
        "Outbox worker запущен. batch_size=%d, max_retries=%d",
        settings.OUTBOX_BATCH_SIZE,
        settings.OUTBOX_MAX_RETRIES,
    )

    last_recovery_at = 0.0
    recovery_interval = settings.OUTBOX_STUCK_THRESHOLD_MINUTES * 60

    while not shutdown_event.is_set():
        try:
            async with session_factory() as session:
                now = time.monotonic()
                if now - last_recovery_at >= recovery_interval:
                    await recover_stuck_events(session, settings.OUTBOX_STUCK_THRESHOLD_MINUTES)
                    last_recovery_at = now
                processed = await process_batch(
                    session=session,
                    exchange=exchange,
                    max_retries=settings.OUTBOX_MAX_RETRIES,
                    batch_size=settings.OUTBOX_BATCH_SIZE,
                )

            if processed == 0:
                # Очередь пуста - ждём, но выходим сразу при сигнале завершения
                await _wait_or_stop(shutdown_event, settings.OUTBOX_POLL_INTERVAL)
            else:
                logger.debug("Обработано событий: %d", processed)

        except aio_pika.exceptions.AMQPConnectionError as exc:
            # connect_robust переподключает connection, но channel нужно пересоздать вручную
            logger.warning("Потеряно соединение с RabbitMQ: %s — пересоздаю channel", exc)
            try:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=settings.OUTBOX_BATCH_SIZE)
                exchange = await setup_rabbitmq(channel)
                logger.info("RabbitMQ channel пересоздан успешно")
            except Exception as e:
                logger.error("Не удалось пересоздать channel RabbitMQ: %s", e)
                await _wait_or_stop(shutdown_event, settings.OUTBOX_POLL_INTERVAL)

        except Exception as exc:
            logger.exception("Неожиданная ошибка в outbox worker: %s", exc)
            await _wait_or_stop(shutdown_event, settings.OUTBOX_POLL_INTERVAL)

    await connection.close()
    await engine.dispose()
    logger.info("Outbox worker остановлен")


if __name__ == "__main__":
    from app.core.logging_config import setup_logging
    setup_logging(settings.LOG_LEVEL)
    asyncio.run(run_worker())
