"""Fix missing unique constraints and add indices.

Revision ID: a004
Revises: a003
Create Date: 2026-04-01
"""
from alembic import op
import sqlalchemy as sa

revision = 'a004'
down_revision = 'a003'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. Fix data_asset unique constraint (required for ON CONFLICT)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'data_asset_ref_type_unique'
            ) THEN
                ALTER TABLE data_asset 
                ADD CONSTRAINT data_asset_ref_type_unique UNIQUE (reference_id, asset_type);
            END IF;
        END $$;
    """)

    # 2. Ensure indices from auth_tables.sql exist
    op.execute("CREATE INDEX IF NOT EXISTS idx_data_asset_owner ON data_asset (owner_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_data_asset_type ON data_asset (asset_type)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_data_asset_ref ON data_asset (reference_id)")
    
    op.execute("CREATE INDEX IF NOT EXISTS idx_collection_owner ON data_asset_collection (owner_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_collection_active ON data_asset_collection (is_active)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_collection_dynamic ON data_asset_collection (is_dynamic)")
    
    op.execute("CREATE INDEX IF NOT EXISTS idx_access_request_org ON access_request (requesting_org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_access_request_status ON access_request (status)")
    
    op.execute("CREATE INDEX IF NOT EXISTS idx_access_grant_org ON access_grant (organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_access_grant_asset ON access_grant (asset_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_access_grant_active ON access_grant (is_active)")

def downgrade() -> None:
    op.execute("ALTER TABLE data_asset DROP CONSTRAINT IF EXISTS data_asset_ref_type_unique")
