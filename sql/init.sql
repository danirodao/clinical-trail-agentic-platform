-- sql/init.sql
-- ═══════════════════════════════════════════════════════════════
-- Clinical Trial Platform — Database Schema
-- Column sizes are generous to handle PDF-extracted data safely
-- ═══════════════════════════════════════════════════════════════
\connect clinical_trials ctuser;
-- ── CLINICAL TRIAL ──
CREATE TABLE IF NOT EXISTS clinical_trial (
    trial_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    nct_id VARCHAR(50) UNIQUE NOT NULL,
    org_study_id VARCHAR(100),
    title TEXT NOT NULL,
    official_title TEXT,
    acronym VARCHAR(50),
    study_type VARCHAR(100) NOT NULL DEFAULT 'Interventional',
    phase VARCHAR(50),
    allocation VARCHAR(100),
    intervention_model VARCHAR(255),
    masking VARCHAR(255),
    primary_purpose VARCHAR(100),
    overall_status VARCHAR(100) NOT NULL DEFAULT 'Unknown',
    start_date DATE,
    completion_date DATE,
    last_update_date TIMESTAMP WITH TIME ZONE,
    enrollment_count INTEGER DEFAULT 0,
    enrollment_type VARCHAR(50) DEFAULT 'Anticipated',
    lead_sponsor VARCHAR(500),
    collaborators TEXT [],
    oversight_authority TEXT [],
    regions TEXT [] NOT NULL DEFAULT '{}',
    countries TEXT [],
    site_locations JSONB DEFAULT '[]',
    therapeutic_area VARCHAR(255),
    condition_mesh_terms TEXT [],
    tags JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    source_system VARCHAR(255),
    data_version INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS trial_arm (
    arm_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    trial_id UUID NOT NULL REFERENCES clinical_trial (trial_id) ON DELETE CASCADE,
    arm_label TEXT NOT NULL,
    arm_type VARCHAR(100),
    description TEXT,
    target_enrollment INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS intervention (
    intervention_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    trial_id UUID NOT NULL REFERENCES clinical_trial (trial_id) ON DELETE CASCADE,
    arm_id UUID REFERENCES trial_arm (arm_id),
    intervention_type VARCHAR(100) NOT NULL DEFAULT 'Drug',
    name TEXT NOT NULL,
    generic_name TEXT,
    rxnorm_code VARCHAR(50),
    ndc_code VARCHAR(50),
    dosage_form VARCHAR(255),
    dose_value NUMERIC,
    dose_unit VARCHAR(50),
    route VARCHAR(100),
    frequency VARCHAR(255),
    duration VARCHAR(255),
    description TEXT
);

CREATE TABLE IF NOT EXISTS eligibility_criteria (
    criteria_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    trial_id UUID NOT NULL REFERENCES clinical_trial (trial_id) ON DELETE CASCADE,
    criteria_type VARCHAR(50) NOT NULL,
    description TEXT NOT NULL,
    structured_criteria JSONB,
    min_age INTEGER,
    max_age INTEGER,
    gender VARCHAR(50) DEFAULT 'All',
    healthy_volunteers BOOLEAN DEFAULT false
);

CREATE TABLE IF NOT EXISTS outcome_measure (
    outcome_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    trial_id UUID NOT NULL REFERENCES clinical_trial (trial_id) ON DELETE CASCADE,
    outcome_type VARCHAR(50) NOT NULL DEFAULT 'Other',
    measure TEXT NOT NULL,
    time_frame TEXT,
    description TEXT
);

-- ── PATIENT ──
CREATE TABLE IF NOT EXISTS patient (
    patient_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    subject_id VARCHAR(255) NOT NULL,
    site_id VARCHAR(255),
    age INTEGER,
    age_unit VARCHAR(20) DEFAULT 'YEARS',
    sex VARCHAR(20),
    race VARCHAR(255),
    ethnicity VARCHAR(255),
    country VARCHAR(100),
    enrollment_date DATE,
    randomization_date DATE,
    arm_assigned TEXT,
    disposition_status VARCHAR(100),
    disposition_reason TEXT,
    phi_salt VARCHAR(128),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE TABLE IF NOT EXISTS patient_trial_enrollment (
    enrollment_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    patient_id UUID NOT NULL REFERENCES patient (patient_id) ON DELETE CASCADE,
    trial_id UUID NOT NULL REFERENCES clinical_trial (trial_id) ON DELETE CASCADE,
    arm_id UUID REFERENCES trial_arm (arm_id),
    enrollment_date DATE,
    completion_date DATE,
    status VARCHAR(100),
    UNIQUE (patient_id, trial_id)
);

CREATE TABLE IF NOT EXISTS patient_condition (
    condition_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    patient_id UUID NOT NULL REFERENCES patient (patient_id) ON DELETE CASCADE,
    condition_name TEXT NOT NULL,
    icd10_code VARCHAR(20),
    mesh_term TEXT,
    snomed_code VARCHAR(50),
    onset_date DATE,
    resolution_date DATE,
    is_ongoing BOOLEAN DEFAULT true,
    severity VARCHAR(50),
    body_system VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS patient_medication (
    medication_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    patient_id UUID NOT NULL REFERENCES patient (patient_id) ON DELETE CASCADE,
    medication_name TEXT NOT NULL,
    rxnorm_code VARCHAR(50),
    ndc_code VARCHAR(50),
    dose_value NUMERIC,
    dose_unit VARCHAR(50),
    route VARCHAR(100),
    frequency VARCHAR(255),
    indication TEXT,
    start_date DATE,
    end_date DATE,
    is_ongoing BOOLEAN DEFAULT true
);

CREATE TABLE IF NOT EXISTS adverse_event (
    ae_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    patient_id UUID NOT NULL REFERENCES patient (patient_id) ON DELETE CASCADE,
    trial_id UUID REFERENCES clinical_trial (trial_id),
    ae_term TEXT NOT NULL,
    meddra_pt VARCHAR(255),
    meddra_soc VARCHAR(255),
    severity VARCHAR(50),
    serious BOOLEAN DEFAULT false,
    causality VARCHAR(100),
    outcome VARCHAR(100),
    onset_date DATE,
    resolution_date DATE,
    action_taken VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS lab_result (
    lab_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    patient_id UUID NOT NULL REFERENCES patient (patient_id) ON DELETE CASCADE,
    trial_id UUID REFERENCES clinical_trial (trial_id),
    test_name VARCHAR(255) NOT NULL,
    loinc_code VARCHAR(50),
    result_value NUMERIC,
    result_unit VARCHAR(100),
    reference_low NUMERIC,
    reference_high NUMERIC,
    abnormal_flag VARCHAR(20),
    specimen_type VARCHAR(100),
    collection_date DATE,
    visit_name VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS vital_sign (
    vital_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    patient_id UUID NOT NULL REFERENCES patient (patient_id) ON DELETE CASCADE,
    trial_id UUID REFERENCES clinical_trial (trial_id),
    test_name VARCHAR(255) NOT NULL,
    result_value NUMERIC NOT NULL,
    result_unit VARCHAR(50),
    position VARCHAR(50),
    collection_date DATE,
    visit_name VARCHAR(255)
);

-- ── COHORT ──
CREATE TABLE IF NOT EXISTS cohort (
    cohort_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_by VARCHAR(255) NOT NULL,
    organization_id VARCHAR(255) NOT NULL,
    filter_criteria JSONB NOT NULL DEFAULT '{}',
    patient_count INTEGER DEFAULT 0,
    trial_count INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    is_dynamic BOOLEAN DEFAULT true,
    last_evaluated_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cohort_patient (
    cohort_id UUID NOT NULL REFERENCES cohort (cohort_id) ON DELETE CASCADE,
    patient_id UUID NOT NULL REFERENCES patient (patient_id) ON DELETE CASCADE,
    included_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    inclusion_reason TEXT,
    PRIMARY KEY (cohort_id, patient_id)
);

CREATE TABLE IF NOT EXISTS cohort_trial (
    cohort_id UUID NOT NULL REFERENCES cohort (cohort_id) ON DELETE CASCADE,
    trial_id UUID NOT NULL REFERENCES clinical_trial (trial_id) ON DELETE CASCADE,
    PRIMARY KEY (cohort_id, trial_id)
);

-- ── INGESTION TRACKING ──
CREATE TABLE IF NOT EXISTS ingestion_log (
    log_id UUID PRIMARY KEY DEFAULT gen_random_uuid (),
    event_id VARCHAR(100) NOT NULL UNIQUE,
    nct_id VARCHAR(50) NOT NULL,
    object_key TEXT NOT NULL,
    trial_id UUID REFERENCES clinical_trial (trial_id),
    status VARCHAR(50) NOT NULL DEFAULT 'processing',
    patient_count INTEGER DEFAULT 0,
    chunk_count INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    completed_at TIMESTAMP WITH TIME ZONE,
    processing_duration NUMERIC
);

-- ── INDEXES ──
CREATE INDEX IF NOT EXISTS idx_patient_trial ON patient_trial_enrollment (trial_id);

CREATE INDEX IF NOT EXISTS idx_patient_condition ON patient_condition (patient_id);

CREATE INDEX IF NOT EXISTS idx_patient_medication ON patient_medication (patient_id);

CREATE INDEX IF NOT EXISTS idx_adverse_event_patient ON adverse_event (patient_id);

CREATE INDEX IF NOT EXISTS idx_adverse_event_trial ON adverse_event (trial_id);

CREATE INDEX IF NOT EXISTS idx_lab_result_patient ON lab_result (patient_id);

CREATE INDEX IF NOT EXISTS idx_clinical_trial_status ON clinical_trial (overall_status);

CREATE INDEX IF NOT EXISTS idx_clinical_trial_area ON clinical_trial (therapeutic_area);

CREATE INDEX IF NOT EXISTS idx_ingestion_log_nct ON ingestion_log (nct_id);

CREATE INDEX IF NOT EXISTS idx_ingestion_log_status ON ingestion_log (status);