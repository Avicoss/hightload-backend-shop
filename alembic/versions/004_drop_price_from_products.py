"""drop price column from products

Revision ID: 004
Revises: 003
Create Date: 2026-05-15 00:00:00.000000

price убран из модели - задание не требует цены, поле усложняло seed и тест
"""
import sqlalchemy as sa
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("products", "price")


def downgrade() -> None:
    op.add_column(
        "products",
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
    )
