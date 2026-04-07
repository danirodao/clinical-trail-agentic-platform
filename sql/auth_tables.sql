\connect clinical_trials ctuser;

-- ═══════════════════════════════════════════════════════════════
-- Fix: add unique constraint for idempotent publishing
-- ═══════════════════════════════════════════════════════════════

-- Individual trial assets (one per trial, atomic auth unit)
CREATE TABLE IF NOT EXISTS data_asset (
    asset_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    asset_type VARCHAR(50) NOT NULL DEFAULT 'clinical_trial' CHECK (
        asset_type IN (
            'clinical_trial',
            'patient_cohort',
            'dataset'
        )
    ),
    reference_id UUID NOT NULL,
    owner_id VARCHAR(255) NOT NULL,
    title VARCHAR(500),
    description TEXT,
    sensitivity_level VARCHAR(20) DEFAULT 'standard' CHECK (
        sensitivity_level IN (
            'public',
            'standard',
            'sensitive',
            'restricted'
        )
    ),
    therapeutic_area VARCHAR(100),
    tags JSONB DEFAULT '[]'::jsonb,
    published_at TIMESTAMPTZ DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    -- Prevent duplicate trial publishing
    UNIQUE (reference_id, asset_type)
);

CREATE INDEX IF NOT EXISTS idx_data_asset_owner ON data_asset (owner_id);

CREATE INDEX IF NOT EXISTS idx_data_asset_type ON data_asset (asset_type);

CREATE INDEX IF NOT EXISTS idx_data_asset_ref ON data_asset (reference_id);

-- ═══════════════════════════════════════════════════════════════
-- Collections: filter-based logical groupings of trials
-- Domain owner defines criteria; system finds matching trials
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS data_asset_collection (
    collection_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              VARCHAR(500) NOT NULL,
    description       TEXT,
    owner_id          VARCHAR(255) NOT NULL,

-- The filter that defines this collection
filter_criteria JSONB NOT NULL DEFAULT '{}'::jsonb,

-- Sensitivity applies to all trials in the collection
sensitivity_level VARCHAR(20) DEFAULT 'standard' CHECK (
    sensitivity_level IN (
        'public',
        'standard',
        'sensitive',
        'restricted'
    )
),

-- Dynamic: auto-includes new trials matching the filter on ingestion
-- Static: snapshot at creation time, never auto-updates
is_dynamic BOOLEAN DEFAULT TRUE,

-- Denormalized summary (updated on publish and refresh)
trial_count       INTEGER DEFAULT 0,
    total_patients    INTEGER DEFAULT 0,
    total_enrollment  INTEGER DEFAULT 0,
    therapeutic_areas TEXT[] DEFAULT '{}',
    phases            TEXT[] DEFAULT '{}',
    study_types       TEXT[] DEFAULT '{}',
    regions           TEXT[] DEFAULT '{}',
    countries         TEXT[] DEFAULT '{}',

    is_active         BOOLEAN DEFAULT TRUE,
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_collection_owner ON data_asset_collection (owner_id);

CREATE INDEX IF NOT EXISTS idx_collection_active ON data_asset_collection (is_active);

CREATE INDEX IF NOT EXISTS idx_collection_dynamic ON data_asset_collection (is_dynamic);

-- Junction: which assets belong to which collection
CREATE TABLE IF NOT EXISTS collection_asset (
    collection_id UUID NOT NULL REFERENCES data_asset_collection (collection_id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES data_asset (asset_id) ON DELETE CASCADE,
    trial_id UUID NOT NULL, -- denormalized for fast queries
    added_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (collection_id, asset_id)
);

CREATE INDEX IF NOT EXISTS idx_collection_asset_trial ON collection_asset (trial_id);

-- Access requests: can target a single asset OR a collection
CREATE TABLE IF NOT EXISTS access_request (
    request_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    asset_id UUID REFERENCES data_asset (asset_id),
    collection_id UUID REFERENCES data_asset_collection (collection_id),
    requesting_user_id VARCHAR(255) NOT NULL,
    requesting_org_id VARCHAR(255) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending' CHECK (
        status IN (
            'pending',
            'approved',
            'rejected',
            'revoked',
            'expired'
        )
    ),
    justification TEXT NOT NULL,
    scope JSONB DEFAULT '{}'::jsonb,
    reviewed_by VARCHAR(255),
    reviewed_at TIMESTAMPTZ,
    review_notes TEXT,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    -- Must target either an asset or a collection
    CHECK (
        asset_id IS NOT NULL
        OR collection_id IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_access_request_asset ON access_request (asset_id);

CREATE INDEX IF NOT EXISTS idx_access_request_coll ON access_request (collection_id);

CREATE INDEX IF NOT EXISTS idx_access_request_org ON access_request (requesting_org_id);

CREATE INDEX IF NOT EXISTS idx_access_request_status ON access_request (status);

-- Access grants: always per-trial (even if originated from collection request)
CREATE TABLE IF NOT EXISTS access_grant (
    grant_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    request_id UUID REFERENCES access_request (request_id),
    asset_id UUID NOT NULL REFERENCES data_asset (asset_id),
    collection_id UUID REFERENCES data_asset_collection (collection_id),
    organization_id VARCHAR(255) NOT NULL,
    scope JSONB DEFAULT '{}'::jsonb,
    granted_by VARCHAR(255) NOT NULL,
    granted_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    revoked_by VARCHAR(255),
    revoke_reason TEXT,
    is_active BOOLEAN GENERATED ALWAYS AS (
        revoked_at IS NULL
        AND expires_at > NOW()
    ) STORED,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_access_grant_org ON access_grant (organization_id);

CREATE INDEX IF NOT EXISTS idx_access_grant_asset ON access_grant (asset_id);

CREATE INDEX IF NOT EXISTS idx_access_grant_coll ON access_grant (collection_id);

CREATE INDEX IF NOT EXISTS idx_access_grant_active ON access_grant (is_active);

-- Researcher assignments (unchanged from before)
CREATE TABLE IF NOT EXISTS researcher_assignment (
    assignment_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    researcher_id VARCHAR(255) NOT NULL,
    organization_id VARCHAR(255) NOT NULL,
    trial_id UUID REFERENCES clinical_trial (trial_id),
    cohort_id UUID REFERENCES cohort (cohort_id),
    access_level VARCHAR(20) DEFAULT 'individual' CHECK (
        access_level IN ('individual', 'aggregate')
    ),
    assigned_by VARCHAR(255) NOT NULL,
    assigned_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    revoked_by VARCHAR(255),
    is_active BOOLEAN GENERATED ALWAYS AS (
        revoked_at IS NULL
        AND expires_at > NOW()
    ) STORED,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CHECK (
        trial_id IS NOT NULL
        OR cohort_id IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_researcher_assign_res ON researcher_assignment (researcher_id);

CREATE INDEX IF NOT EXISTS idx_researcher_assign_org ON researcher_assignment (organization_id);

-- Audit log (unchanged)
CREATE TABLE IF NOT EXISTS auth_audit_log (
    log_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    action VARCHAR(50) NOT NULL,
    actor_id VARCHAR(255) NOT NULL,
    actor_role VARCHAR(50),
    target_type VARCHAR(50),
    target_id VARCHAR(255),
    details JSONB DEFAULT '{}'::jsonb,
    ip_address INET,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_actor ON auth_audit_log (actor_id);

CREATE INDEX IF NOT EXISTS idx_audit_action ON auth_audit_log (action);

CREATE INDEX IF NOT EXISTS idx_audit_created ON auth_audit_log (created_at);