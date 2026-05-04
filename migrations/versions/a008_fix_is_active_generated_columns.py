"""Fix is_active maintenance for grant/assignment tables.

Revision ID: a008
Revises: a007
Create Date: 2026-05-03
"""

from alembic import op


revision = "a008"
down_revision = "a007_governance_purpose_catalog"
branch_labels = None
depends_on = None


def _ensure_is_active_boolean_column(table_name: str) -> None:
    op.execute(
        f"""
        DO $$
        DECLARE
            column_exists boolean;
            gen_state text;
        BEGIN
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = '{table_name}'
                  AND column_name = 'is_active'
            ) INTO column_exists;

            IF NOT column_exists THEN
                ALTER TABLE {table_name}
                    ADD COLUMN is_active BOOLEAN;
            END IF;

            SELECT is_generated
              INTO gen_state
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = '{table_name}'
              AND column_name = 'is_active';

            IF gen_state = 'ALWAYS' THEN
                ALTER TABLE {table_name} DROP COLUMN IF EXISTS is_active;
                ALTER TABLE {table_name}
                    ADD COLUMN is_active BOOLEAN;
            END IF;
        END
        $$;
        """
    )


def _install_is_active_trigger(table_name: str) -> None:
    trigger_name = f"trg_{table_name}_set_is_active"
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {table_name}_set_is_active_fn()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.is_active := (NEW.revoked_at IS NULL AND NEW.expires_at > NOW());
            RETURN NEW;
        END;
        $$;
        """
    )

    op.execute(
        f"""
        DROP TRIGGER IF EXISTS {trigger_name} ON {table_name};
        CREATE TRIGGER {trigger_name}
        BEFORE INSERT OR UPDATE OF revoked_at, expires_at
        ON {table_name}
        FOR EACH ROW
        EXECUTE FUNCTION {table_name}_set_is_active_fn();
        """
    )

    # Backfill existing rows once after column and trigger are in place.
    op.execute(
        f"""
        UPDATE {table_name}
        SET is_active = (revoked_at IS NULL AND expires_at > NOW())
        WHERE is_active IS DISTINCT FROM (revoked_at IS NULL AND expires_at > NOW());
        """
    )

    op.execute(
        f"""
        ALTER TABLE {table_name}
        ALTER COLUMN is_active SET NOT NULL;
        """
    )


def upgrade() -> None:
    _ensure_is_active_boolean_column("access_grant")
    _ensure_is_active_boolean_column("researcher_assignment")

    _install_is_active_trigger("access_grant")
    _install_is_active_trigger("researcher_assignment")

    op.execute("CREATE INDEX IF NOT EXISTS idx_access_grant_active ON access_grant (is_active)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_researcher_assign_active ON researcher_assignment (is_active)"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_access_grant_set_is_active ON access_grant")
    op.execute("DROP FUNCTION IF EXISTS access_grant_set_is_active_fn()")
    op.execute("DROP TRIGGER IF EXISTS trg_researcher_assignment_set_is_active ON researcher_assignment")
    op.execute("DROP FUNCTION IF EXISTS researcher_assignment_set_is_active_fn()")
