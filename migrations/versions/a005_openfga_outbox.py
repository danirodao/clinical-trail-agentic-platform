"""OpenFGA tuple outbox

Revision ID: a005
Create Date: 2026-04-27
"""
from alembic import op

revision = "a005"
down_revision = "a004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS openfga_tuple_outbox (
            id BIGSERIAL PRIMARY KEY,
            operation VARCHAR(10) NOT NULL CHECK (operation IN ('write', 'delete')),
            tuple_user TEXT NOT NULL,
            tuple_relation TEXT NOT NULL,
            tuple_object TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'processing', 'failed', 'applied', 'dead')),
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            source TEXT,
            correlation_id TEXT,
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            processed_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_openfga_outbox_pending
        ON openfga_tuple_outbox (status, available_at, id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_openfga_outbox_correlation
        ON openfga_tuple_outbox (correlation_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS openfga_tuple_outbox")
