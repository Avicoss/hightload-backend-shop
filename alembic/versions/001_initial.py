"""initial schema

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enum-типы создаём отдельно - алембик не всегда делает это автоматически
    op.execute("CREATE TYPE userstatus AS ENUM ('active', 'banned')")
    op.execute("CREATE TYPE outboxstatus AS ENUM ('pending', 'processing', 'sent', 'dlq')")

    op.create_table(
        "users",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM("active", "banned", name="userstatus", create_type=False),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "products",
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("product_id"),
        # Страховка - остаток не должен уходить в минус на уровне БД
        sa.CheckConstraint("stock >= 0", name="products_stock_non_negative"),
    )

    op.create_table(
        "orders",
        sa.Column("order_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("purchased_count", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("order_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.product_id"]),
        sa.UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key"),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending", "processing", "sent", "dlq",
                name="outboxstatus",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        # updated_at используется для обнаружения зависших processing-событий
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Триггер для автообновления updated_at
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ language 'plpgsql';
        """
    )
    op.execute(
        """
        CREATE TRIGGER outbox_events_updated_at
        BEFORE UPDATE ON outbox_events
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        """
    )

    # Индекс для воркера - выборка пендинг по времени следующего ретрая
    op.create_index(
        "ix_outbox_events_status_next_retry",
        "outbox_events",
        ["status", "next_retry_at"],
    )

def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_table("orders")
    op.drop_table("products")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS outboxstatus")
    op.execute("DROP TYPE IF EXISTS userstatus")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column")
