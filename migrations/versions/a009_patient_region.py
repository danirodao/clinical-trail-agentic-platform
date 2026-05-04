"""Add patient.region column with country→region lookup and auto-trigger.

Revision ID: a009
Revises: a008
Create Date: 2026-05-03
"""

from alembic import op


revision = "a009"
down_revision = "a008"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Canonical country → region mapping.
# Intentionally verbose so it can be extended without code changes.
# ---------------------------------------------------------------------------
_COUNTRY_REGION_ROWS = [
    # North America
    ("United States", "North America"),
    ("Canada", "North America"),
    ("Mexico", "North America"),
    # Europe
    ("United Kingdom", "Europe"),
    ("Germany", "Europe"),
    ("France", "Europe"),
    ("Spain", "Europe"),
    ("Italy", "Europe"),
    ("Netherlands", "Europe"),
    ("Belgium", "Europe"),
    ("Switzerland", "Europe"),
    ("Sweden", "Europe"),
    ("Norway", "Europe"),
    ("Denmark", "Europe"),
    ("Finland", "Europe"),
    ("Poland", "Europe"),
    ("Portugal", "Europe"),
    ("Austria", "Europe"),
    ("Czech Republic", "Europe"),
    ("Hungary", "Europe"),
    ("Romania", "Europe"),
    ("Greece", "Europe"),
    # Latin America
    ("Brazil", "Latin America"),
    ("Argentina", "Latin America"),
    ("Colombia", "Latin America"),
    ("Chile", "Latin America"),
    ("Peru", "Latin America"),
    ("Ecuador", "Latin America"),
    ("Venezuela", "Latin America"),
    ("Uruguay", "Latin America"),
    ("Paraguay", "Latin America"),
    ("Bolivia", "Latin America"),
    # Asia-Pacific
    ("Japan", "Asia-Pacific"),
    ("South Korea", "Asia-Pacific"),
    ("Australia", "Asia-Pacific"),
    ("China", "Asia-Pacific"),
    ("India", "Asia-Pacific"),
    ("Taiwan", "Asia-Pacific"),
    ("Singapore", "Asia-Pacific"),
    ("Hong Kong", "Asia-Pacific"),
    ("Thailand", "Asia-Pacific"),
    ("Malaysia", "Asia-Pacific"),
    ("New Zealand", "Asia-Pacific"),
    # Middle East & Africa
    ("Israel", "Middle East & Africa"),
    ("Turkey", "Middle East & Africa"),
    ("South Africa", "Middle East & Africa"),
    ("Egypt", "Middle East & Africa"),
    ("Saudi Arabia", "Middle East & Africa"),
    ("United Arab Emirates", "Middle East & Africa"),
]


def upgrade() -> None:
    # ── 1. country_region lookup table ───────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS country_region (
            country VARCHAR(200) PRIMARY KEY,
            region  VARCHAR(100) NOT NULL
        );
    """)

    # Upsert all rows so re-runs are idempotent
    values_sql = ", ".join(
        f"('{country.replace(chr(39), chr(39)+chr(39))}', '{region}')"
        for country, region in _COUNTRY_REGION_ROWS
    )
    op.execute(f"""
        INSERT INTO country_region (country, region)
        VALUES {values_sql}
        ON CONFLICT (country) DO UPDATE
            SET region = EXCLUDED.region;
    """)

    # ── 2. Add region column to patient (idempotent) ─────────────────────
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name   = 'patient'
                  AND column_name  = 'region'
            ) THEN
                ALTER TABLE patient ADD COLUMN region VARCHAR(100);
            END IF;
        END
        $$;
    """)

    # ── 3. Backfill existing rows ─────────────────────────────────────────
    op.execute("""
        UPDATE patient p
        SET    region = cr.region
        FROM   country_region cr
        WHERE  cr.country = p.country
          AND  p.region IS NULL;
    """)

    # ── 4. Index for fast region-based patient filtering ─────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_patient_region
            ON patient (region);
    """)

    # ── 5. Trigger: auto-resolve region on INSERT / UPDATE of country ─────
    op.execute("""
        CREATE OR REPLACE FUNCTION patient_set_region_fn()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            SELECT cr.region
              INTO NEW.region
              FROM country_region cr
             WHERE cr.country = NEW.country
             LIMIT 1;
            -- if no mapping found, leave region as-is (NULL or previous value)
            RETURN NEW;
        END;
        $$;
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS trg_patient_set_region ON patient;
        CREATE TRIGGER trg_patient_set_region
        BEFORE INSERT OR UPDATE OF country
        ON patient
        FOR EACH ROW
        EXECUTE FUNCTION patient_set_region_fn();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_patient_set_region ON patient;")
    op.execute("DROP FUNCTION IF EXISTS patient_set_region_fn();")
    op.execute("DROP INDEX IF EXISTS idx_patient_region;")
    op.execute("ALTER TABLE patient DROP COLUMN IF EXISTS region;")
    op.execute("DROP TABLE IF EXISTS country_region;")
