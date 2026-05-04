"""Add conditional tuple payload fields to openfga_tuple_outbox

Revision ID: a006_outbox_conditional_payload
Revises: a005
Create Date: 2026-05-02
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "a006_outbox_conditional_payload"
down_revision = "a005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE openfga_tuple_outbox
        ADD COLUMN IF NOT EXISTS condition_name TEXT
        """
    )
    op.execute(
        """
        ALTER TABLE openfga_tuple_outbox
        ADD COLUMN IF NOT EXISTS condition_context JSONB
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_openfga_outbox_conditional
        ON openfga_tuple_outbox (condition_name)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_openfga_outbox_conditional")
    op.execute("ALTER TABLE openfga_tuple_outbox DROP COLUMN IF EXISTS condition_context")
    op.execute("ALTER TABLE openfga_tuple_outbox DROP COLUMN IF EXISTS condition_name")
