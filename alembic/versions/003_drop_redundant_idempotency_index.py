"""drop redundant idempotency_key index

Revision ID: 003
Revises: 002
Create Date: 2026-05-15 00:00:00.000000

UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key") в таблице orders
уже создаёт уникальный индекс. Отдельный ix_orders_idempotency_key - дубль, занимает место
Эта миграция удаляет его на существующих БД, где 001 успел его создать
"""
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_orders_idempotency_key", table_name="orders")


def downgrade() -> None:
    op.create_index(
        "ix_orders_idempotency_key",
        "orders",
        ["idempotency_key"],
        unique=True,
    )
