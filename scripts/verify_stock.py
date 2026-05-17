"""
Верификация остатков после сценарного теста

Для каждого товара проверяет инвариант:
    initial_stock == final_stock + SUM(purchased_count в orders)

Запуск:
    python scripts/verify_stock.py
"""
import asyncio
import sys

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.db.models import Order, Product

INITIAL_STOCK = 10_000_000
PRODUCT_NAMES = {1: "laptop", 2: "desktop"}


async def verify() -> None:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    errors: list[str] = []

    async with session_factory() as session:
        # Текущий остаток по каждому товару
        stock_rows = (await session.execute(
            select(Product.product_id, Product.stock)
            .where(Product.product_id.in_(PRODUCT_NAMES.keys()))
            .order_by(Product.product_id)
        )).all()

        # Сумма заказанных единиц по каждому товару
        purchased_rows = (await session.execute(
            select(Order.product_id, func.sum(Order.purchased_count).label("total"))
            .where(Order.product_id.in_(PRODUCT_NAMES.keys()))
            .group_by(Order.product_id)
        )).all()

        # Общее количество заказов
        orders_count = (await session.execute(
            select(func.count()).select_from(Order)
        )).scalar()

    purchased_map = {row.product_id: int(row.total or 0) for row in purchased_rows}
    stock_map    = {row.product_id: row.stock for row in stock_rows}

    print(f"\n{'─'*55}")
    print(f"{'Товар':<10} {'Нач.остаток':>13} {'Продано':>10} {'Ост.в БД':>10} {'OK?':>5}")
    print(f"{'─'*55}")

    for pid, name in PRODUCT_NAMES.items():
        final   = stock_map.get(pid, 0)
        sold    = purchased_map.get(pid, 0)
        check   = INITIAL_STOCK == final + sold
        status  = "✓" if check else "✗ ОШИБКА"
        print(f"{name:<10} {INITIAL_STOCK:>13,} {sold:>10,} {final:>10,} {status:>5}")
        if not check:
            errors.append(
                f"{name}: {INITIAL_STOCK:,} != {final:,} + {sold:,} "
                f"(расхождение {INITIAL_STOCK - final - sold:+,})"
            )

    print(f"{'─'*55}")
    print(f"Всего заказов в БД: {orders_count:,}\n")

    if errors:
        print("ОШИБКИ ВЕРИФИКАЦИИ:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    else:
        print("Верификация пройдена — остаток сходится.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(verify())
