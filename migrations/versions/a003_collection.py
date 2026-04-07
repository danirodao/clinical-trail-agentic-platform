"""Collection tables and access_request collection_id

Revision ID: 003
Create Date: 2026-03-31
"""
from alembic import op

revision = 'a003'
down_revision = 'a002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Collection table
    op.execute("""
        CREATE TABLE IF NOT EXISTS data_asset_collection (
            collection_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(500) NOT NULL,
            description TEXT,
            owner_id VARCHAR(255) NOT NULL,
            filter_criteria JSONB NOT NULL DEFAULT '{}'::jsonb,
            sensitivity_level VARCHAR(20) DEFAULT 'standard',
            is_dynamic BOOLEAN DEFAULT TRUE,
            trial_count INTEGER DEFAULT 0,
            total_patients INTEGER DEFAULT 0,
            total_enrollment INTEGER DEFAULT 0,
            therapeutic_areas TEXT[] DEFAULT '{}',
            phases TEXT[] DEFAULT '{}',
            study_types TEXT[] DEFAULT '{}',
            regions TEXT[] DEFAULT '{}',
            countries TEXT[] DEFAULT '{}',
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_collection_owner ON data_asset_collection(owner_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_collection_dynamic ON data_asset_collection(is_dynamic)")

    # Collection-asset junction
    op.execute("""
        CREATE TABLE IF NOT EXISTS collection_asset (
            collection_id UUID NOT NULL REFERENCES data_asset_collection(collection_id) ON DELETE CASCADE,
            asset_id UUID NOT NULL REFERENCES data_asset(asset_id) ON DELETE CASCADE,
            trial_id UUID NOT NULL,
            added_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (collection_id, asset_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_collection_asset_trial ON collection_asset(trial_id)")

    # Add collection_id to access_request (if not exists)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'access_request' AND column_name = 'collection_id'
            ) THEN
                ALTER TABLE access_request
                ADD COLUMN collection_id UUID REFERENCES data_asset_collection(collection_id);
            END IF;
        END $$
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_access_request_coll ON access_request(collection_id)")

    # Add collection_id to access_grant (if not exists)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'access_grant' AND column_name = 'collection_id'
            ) THEN
                ALTER TABLE access_grant
                ADD COLUMN collection_id UUID REFERENCES data_asset_collection(collection_id);
            END IF;
        END $$
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_access_grant_coll ON access_grant(collection_id)")

    # Relax access_request: asset_id can now be NULL (collection requests don't have one)
    op.execute("""
        DO $$
        BEGIN
            ALTER TABLE access_request ALTER COLUMN asset_id DROP NOT NULL;
        EXCEPTION
            WHEN others THEN NULL;
        END $$
    """)

    # Add CHECK: must have asset_id OR collection_id
    op.execute("""
        DO $$
        BEGIN
            ALTER TABLE access_request
            ADD CONSTRAINT chk_request_target
            CHECK (asset_id IS NOT NULL OR collection_id IS NOT NULL);
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE access_grant DROP COLUMN IF EXISTS collection_id")
    op.execute("ALTER TABLE access_request DROP COLUMN IF EXISTS collection_id")
    op.execute("DROP TABLE IF EXISTS collection_asset")
    op.execute("DROP TABLE IF EXISTS data_asset_collection")