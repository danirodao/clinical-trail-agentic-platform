"""Governance purpose catalog (global + owner-defined)

Revision ID: a007_governance_purpose_catalog
Revises: a006_outbox_conditional_payload
Create Date: 2026-05-03
"""

from alembic import op


revision = "a007_governance_purpose_catalog"
down_revision = "a006_outbox_conditional_payload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS governance_purpose (
            purpose_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_id VARCHAR(255),
            purpose_key VARCHAR(120) NOT NULL,
            label VARCHAR(255) NOT NULL,
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (owner_id, purpose_key)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_governance_purpose_global_key
        ON governance_purpose (purpose_key)
        WHERE owner_id IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_governance_purpose_owner
        ON governance_purpose (owner_id, is_active)
        """
    )

    op.execute(
        """
        INSERT INTO governance_purpose (owner_id, purpose_key, label, description, created_by)
        VALUES
            (NULL, 'clinical_research', 'Clinical Research', 'General clinical research purpose', 'system'),
            (NULL, 'regulatory_submission', 'Regulatory Submission', 'Regulatory dossier preparation and submission', 'system'),
            (NULL, 'safety_monitoring', 'Safety Monitoring', 'Ongoing safety monitoring and surveillance', 'system'),
            (NULL, 'pharmacovigilance', 'Pharmacovigilance', 'Drug safety signal detection and risk management', 'system')
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_governance_purpose_owner")
    op.execute("DROP INDEX IF EXISTS uq_governance_purpose_global_key")
    op.execute("DROP TABLE IF EXISTS governance_purpose")
