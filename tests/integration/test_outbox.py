"""
Интеграционные тесты Outbox Worker

Тестируем логику воркера напрямую через функции (без запуска процесса)

Замечания по верификации:
- expire_on_commit=False означает, что identity map хранит объекты после коммита
- После операций внутри _publish_event / recover_stuck_events нужно явно обновлять
  объекты через select с populate_existing=True
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import OutboxEvent, OutboxStatus
from app.worker.outbox_worker import (
    _calc_next_retry_at,
    _publish_event,
    process_batch,
    recover_stuck_events,
)


# ─── Тесты логики retry/backoff (без инфраструктуры) ─────────────────────────

async def test_backoff_grows_exponentially():
    """Задержка между попытками растёт экспоненциально
    Jitter замокан в 0.5, чтобы тест был детерминированным:
    без мока jitter=0.999 на retry=0 даёт 1.999, что больше чем 2.001 при jitter=0.001 на retry=1
    """
    with patch("app.worker.outbox_worker.random.uniform", return_value=0.5):
        delays = []
        for i in range(5):
            t = _calc_next_retry_at(i)
            delay = (t - datetime.now(tz=timezone.utc)).total_seconds()
            delays.append(delay)

    for i in range(1, len(delays)):
        assert delays[i] > delays[i - 1], (
            f"delay[{i}]={delays[i]:.2f} <= delay[{i - 1}]={delays[i - 1]:.2f}"
        )


async def test_backoff_capped_at_300_seconds():
    """Задержка не превышает 301 секунду (300 + jitter max=1)."""
    with patch("app.worker.outbox_worker.random.uniform", return_value=1.0):
        t = _calc_next_retry_at(100)
        delay = (t - datetime.now(tz=timezone.utc)).total_seconds()
    assert delay <= 301


# ─── Вспомогательная функция для получения свежих данных из БД ───────────────

async def _reload(session: AsyncSession, cls, pk: int):
    """Возвращает свежую копию объекта из БД, минуя identity map."""
    result = await session.execute(
        select(cls)
        .where(cls.id == pk)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


# ─── Фикстура pending события ─────────────────────────────────────────────────

@pytest_asyncio.fixture
async def pending_event(db_session: AsyncSession) -> OutboxEvent:
    """Создаёт одно pending-событие в тестовой БД."""
    event = OutboxEvent(
        event_type="order.created",
        payload={"order_id": "ord_1", "user_id": "usr_1", "currency": "UAH"},
        status=OutboxStatus.pending,
    )
    db_session.add(event)
    await db_session.commit()
    # Сбрасываем checkout соединения после commit
    # Следующая операция на db_session создаст новое соединение в текущем event loop
    # - это предотвращает "Future attached to a different loop" в teardown
    await db_session.close()
    return event


# ─── Тесты publish / retry ────────────────────────────────────────────────────

async def test_successful_publish_marks_sent(
    db_session: AsyncSession,
    pending_event: OutboxEvent,
):
    """После успешной публикации событие переходит в статус sent."""
    mock_exchange = AsyncMock()

    await _publish_event(
        session=db_session,
        exchange=mock_exchange,
        event=pending_event,
        max_retries=settings.OUTBOX_MAX_RETRIES,
    )

    mock_exchange.publish.assert_called_once()

    updated = await _reload(db_session, OutboxEvent, pending_event.id)
    assert updated.status == OutboxStatus.sent
    assert updated.sent_at is not None
    assert updated.error is None


async def test_failed_publish_increments_retry(
    db_session: AsyncSession,
    pending_event: OutboxEvent,
):
    """При ошибке публикации retry_count растёт, событие остаётся pending."""
    mock_exchange = AsyncMock()
    mock_exchange.publish.side_effect = Exception("Connection refused")

    await _publish_event(
        session=db_session,
        exchange=mock_exchange,
        event=pending_event,
        max_retries=settings.OUTBOX_MAX_RETRIES,
    )

    updated = await _reload(db_session, OutboxEvent, pending_event.id)
    assert updated.status == OutboxStatus.pending
    assert updated.retry_count == 1
    assert updated.error is not None
    assert updated.next_retry_at > datetime.now(tz=timezone.utc)


async def test_max_retries_moves_to_dlq(
    db_session: AsyncSession,
    pending_event: OutboxEvent,
):
    """После достижения max_retries событие переводится в DLQ."""
    mock_exchange = AsyncMock()
    mock_exchange.publish.side_effect = Exception("Permanent failure")

    # Устанавливаем retry_count = max_retries - 1, следующая попытка - последняя
    pending_event.retry_count = settings.OUTBOX_MAX_RETRIES - 1
    await db_session.commit()

    await _publish_event(
        session=db_session,
        exchange=mock_exchange,
        event=pending_event,
        max_retries=settings.OUTBOX_MAX_RETRIES,
    )

    updated = await _reload(db_session, OutboxEvent, pending_event.id)
    assert updated.status == OutboxStatus.dlq
    assert updated.retry_count == settings.OUTBOX_MAX_RETRIES


async def test_process_batch_picks_only_pending(db_session: AsyncSession):
    """process_batch берёт только pending-события с наступившим next_retry_at."""
    # pending с наступившим временем - должен быть обработан
    event_ok = OutboxEvent(
        event_type="order.created",
        payload={"order_id": "ord_1"},
        status=OutboxStatus.pending,
        next_retry_at=datetime.now(tz=timezone.utc) - timedelta(seconds=1),
    )
    # sent - не трогаем
    event_sent = OutboxEvent(
        event_type="order.created",
        payload={"order_id": "ord_2"},
        status=OutboxStatus.sent,
    )
    # pending, но время ещё не наступило - пропускаем
    event_future = OutboxEvent(
        event_type="order.created",
        payload={"order_id": "ord_3"},
        status=OutboxStatus.pending,
        next_retry_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
    )
    db_session.add_all([event_ok, event_sent, event_future])
    await db_session.commit()

    mock_exchange = AsyncMock()
    processed = await process_batch(
        session=db_session,
        exchange=mock_exchange,
        max_retries=settings.OUTBOX_MAX_RETRIES,
        batch_size=100,
    )

    assert processed == 1
    mock_exchange.publish.assert_called_once()


async def test_recover_stuck_events(db_session: AsyncSession):
    """
    События, застрявшие в processing, сбрасываются в pending

    Важно: trigger outbox_events_updated_at перезаписывает updated_at при UPDATE
    Поэтому вставляем событие с нужным updated_at через raw INSERT (trigger
    срабатывает только на UPDATE, не на INSERT)
    """
    stuck_time = datetime.now(tz=timezone.utc) - timedelta(minutes=10)

    # Вставляем через raw SQL - только так можно задать прошлое updated_at
    # (при UPDATE trigger перезапишет поле на now())
    result = await db_session.execute(
        text(
            """
            INSERT INTO outbox_events
                (event_type, payload, status, retry_count, next_retry_at, updated_at, created_at)
            VALUES
                ('order.created', '{"order_id": "ord_stuck"}', 'processing',
                 0, now(), :stuck_time, now())
            RETURNING id
            """
        ),
        {"stuck_time": stuck_time},
    )
    event_id = result.scalar_one()
    await db_session.commit()

    await recover_stuck_events(db_session, threshold_minutes=5)

    # Перечитываем с populate_existing=True - иначе identity map вернёт старые данные
    recovered = await _reload(db_session, OutboxEvent, event_id)
    assert recovered.status == OutboxStatus.pending
