"""Auth tables

Revision ID: 002
Create Date: 2026-03-31
"""
from alembic import op

revision = 'a002'
down_revision = 'a001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS data_asset (
            asset_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            asset_type VARCHAR(50) NOT NULL DEFAULT 'clinical_trial',
            reference_id UUID NOT NULL,
            owner_id VARCHAR(255) NOT NULL,
            title VARCHAR(500),
            description TEXT,
            sensitivity_level VARCHAR(20) DEFAULT 'standard',
            therapeutic_area VARCHAR(100),
            tags JSONB DEFAULT '[]'::jsonb,
            published_at TIMESTAMPTZ DEFAULT NOW(),
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (reference_id, asset_type)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS access_request (
            request_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            asset_id UUID REFERENCES data_asset(asset_id),
            requesting_user_id VARCHAR(255) NOT NULL,
            requesting_org_id VARCHAR(255) NOT NULL,
            status VARCHAR(20) DEFAULT 'pending',
            justification TEXT NOT NULL,
            scope JSONB DEFAULT '{}'::jsonb,
            reviewed_by VARCHAR(255),
            reviewed_at TIMESTAMPTZ,
            review_notes TEXT,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS access_grant (
            grant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            request_id UUID REFERENCES access_request(request_id),
            asset_id UUID NOT NULL REFERENCES data_asset(asset_id),
            organization_id VARCHAR(255) NOT NULL,
            scope JSONB DEFAULT '{}'::jsonb,
            granted_by VARCHAR(255) NOT NULL,
            granted_at TIMESTAMPTZ DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            revoked_by VARCHAR(255),
            revoke_reason TEXT,
            is_active BOOLEAN GENERATED ALWAYS AS (revoked_at IS NULL AND expires_at > NOW()) STORED,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS researcher_assignment (
            assignment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            researcher_id VARCHAR(255) NOT NULL,
            organization_id VARCHAR(255) NOT NULL,
            trial_id UUID REFERENCES clinical_trial(trial_id),
            cohort_id UUID REFERENCES cohort(cohort_id),
            access_level VARCHAR(20) DEFAULT 'individual',
            assigned_by VARCHAR(255) NOT NULL,
            assigned_at TIMESTAMPTZ DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            revoked_by VARCHAR(255),
            is_active BOOLEAN GENERATED ALWAYS AS (revoked_at IS NULL AND expires_at > NOW()) STORED,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CHECK (trial_id IS NOT NULL OR cohort_id IS NOT NULL)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS auth_audit_log (
            log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            action VARCHAR(50) NOT NULL,
            actor_id VARCHAR(255) NOT NULL,
            actor_role VARCHAR(50),
            target_type VARCHAR(50),
            target_id VARCHAR(255),
            details JSONB DEFAULT '{}'::jsonb,
            ip_address INET,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Indexes
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_data_asset_owner ON data_asset(owner_id)",
        "CREATE INDEX IF NOT EXISTS idx_data_asset_ref ON data_asset(reference_id)",
        "CREATE INDEX IF NOT EXISTS idx_access_request_org ON access_request(requesting_org_id)",
        "CREATE INDEX IF NOT EXISTS idx_access_request_status ON access_request(status)",
        "CREATE INDEX IF NOT EXISTS idx_access_grant_org ON access_grant(organization_id)",
        "CREATE INDEX IF NOT EXISTS idx_access_grant_active ON access_grant(is_active)",
        "CREATE INDEX IF NOT EXISTS idx_audit_actor ON auth_audit_log(actor_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_created ON auth_audit_log(created_at)",
    ]:
        op.execute(stmt)


def downgrade() -> None:
    pass