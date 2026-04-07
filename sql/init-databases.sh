#!/bin/bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════
# Create isolated databases and users for each service
# Runs as the postgres superuser during container init
# ═══════════════════════════════════════════════════════════════

echo "Creating databases and users..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL

    -- ── Application Database ──────────────────────────────────
    CREATE USER ctuser WITH PASSWORD 'ctpassword';
    CREATE DATABASE clinical_trials OWNER ctuser;
    GRANT ALL PRIVILEGES ON DATABASE clinical_trials TO ctuser;

    -- ── Keycloak Database ─────────────────────────────────────
    CREATE USER keycloak WITH PASSWORD 'keycloak_secret';
    CREATE DATABASE keycloak OWNER keycloak;
    GRANT ALL PRIVILEGES ON DATABASE keycloak TO keycloak;

    -- ── OpenFGA Database ──────────────────────────────────────
    CREATE USER openfga WITH PASSWORD 'openfga_secret';
    CREATE DATABASE openfga OWNER openfga;
    GRANT ALL PRIVILEGES ON DATABASE openfga TO openfga;

    -- ── Revoke cross-database access ─────────────────────────
    -- By default in PG 15+, public schema CREATE is revoked.
    -- Explicitly deny cross-access for defense in depth.
    REVOKE CONNECT ON DATABASE clinical_trials FROM PUBLIC;
    REVOKE CONNECT ON DATABASE keycloak FROM PUBLIC;
    REVOKE CONNECT ON DATABASE openfga FROM PUBLIC;

    GRANT CONNECT ON DATABASE clinical_trials TO ctuser;
    GRANT CONNECT ON DATABASE keycloak TO keycloak;
    GRANT CONNECT ON DATABASE openfga TO openfga;

EOSQL

echo "✓ All databases and users created"