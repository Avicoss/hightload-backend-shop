"""add indexes on orders foreign keys

Revision ID: 002
Revises: 001
Create Date: 2026-05-15 00:00:00.000000

В PostgreSQL FK не создают индекс автоматически (в отличие от MySQL)
Без этих индексов выборка истории заказов по user_id или product_id - full scan
"""
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_orders_user_id", "orders", ["user_id"])
    op.create_index("ix_orders_product_id", "orders", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_orders_product_id", table_name="orders")
    op.drop_index("ix_orders_user_id", table_name="orders")
