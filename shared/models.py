# generator/models.py
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date, datetime
from enum import Enum
import uuid


# ═══════════════════════════════════════════
# ENUMS (CDISC Controlled Terminology)
# ═══════════════════════════════════════════

class StudyPhase(str, Enum):
    PHASE_1 = "Phase 1"
    PHASE_1_2 = "Phase 1/Phase 2"
    PHASE_2 = "Phase 2"
    PHASE_2_3 = "Phase 2/Phase 3"
    PHASE_3 = "Phase 3"
    PHASE_4 = "Phase 4"
    NOT_APPLICABLE = "N/A"

class StudyType(str, Enum):
    INTERVENTIONAL = "Interventional"
    OBSERVATIONAL = "Observational"
    EXPANDED_ACCESS = "Expanded Access"

class OverallStatus(str, Enum):
    NOT_YET_RECRUITING = "Not yet recruiting"
    RECRUITING = "Recruiting"
    ACTIVE_NOT_RECRUITING = "Active, not recruiting"
    COMPLETED = "Completed"
    TERMINATED = "Terminated"
    SUSPENDED = "Suspended"
    WITHDRAWN = "Withdrawn"

class ArmType(str, Enum):
    EXPERIMENTAL = "Experimental"
    ACTIVE_COMPARATOR = "Active Comparator"
    PLACEBO_COMPARATOR = "Placebo Comparator"
    SHAM_COMPARATOR = "Sham Comparator"
    NO_INTERVENTION = "No Intervention"

class InterventionType(str, Enum):
    DRUG = "Drug"
    BIOLOGICAL = "Biological"
    DEVICE = "Device"
    PROCEDURE = "Procedure"
    RADIATION = "Radiation"
    BEHAVIORAL = "Behavioral"
    DIETARY_SUPPLEMENT = "Dietary Supplement"

class Sex(str, Enum):
    MALE = "M"
    FEMALE = "F"
    UNKNOWN = "U"

class Severity(str, Enum):
    MILD = "Mild"
    MODERATE = "Moderate"
    SEVERE = "Severe"

class Causality(str, Enum):
    RELATED = "Related"
    POSSIBLY_RELATED = "Possibly Related"
    UNLIKELY_RELATED = "Unlikely Related"
    NOT_RELATED = "Not Related"


# ═══════════════════════════════════════════
# CLINICAL TRIAL MODELS
# ═══════════════════════════════════════════

class TrialArm(BaseModel):
    arm_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    arm_label: str
    arm_type: ArmType
    description: str
    target_enrollment: int

class Intervention(BaseModel):
    intervention_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    intervention_type: InterventionType
    name: str
    generic_name: Optional[str] = None
    rxnorm_code: Optional[str] = None
    ndc_code: Optional[str] = None
    dosage_form: Optional[str] = None
    dose_value: Optional[float] = None
    dose_unit: Optional[str] = None
    route: Optional[str] = None
    frequency: Optional[str] = None
    duration: Optional[str] = None
    description: str

class EligibilityCriteria(BaseModel):
    criteria_type: str  # Inclusion or Exclusion
    description: str
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    gender: str = "All"
    healthy_volunteers: bool = False

class OutcomeMeasure(BaseModel):
    outcome_type: str  # Primary, Secondary, Other
    measure: str
    time_frame: str
    description: str

class SiteLocation(BaseModel):
    facility: str
    city: str
    state: Optional[str] = None
    country: str
    zip_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class ClinicalTrial(BaseModel):
    trial_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nct_id: str
    org_study_id: Optional[str] = None
    title: str
    official_title: str
    acronym: Optional[str] = None
    study_type: StudyType
    phase: StudyPhase
    allocation: Optional[str] = None
    intervention_model: Optional[str] = None
    masking: Optional[str] = None
    primary_purpose: Optional[str] = None
    overall_status: OverallStatus
    start_date: date
    completion_date: Optional[date] = None
    last_update_date: datetime
    enrollment_count: int
    enrollment_type: str = "Anticipated"
    lead_sponsor: str
    collaborators: list[str] = []
    oversight_authority: list[str] = []
    regions: list[str] = []
    countries: list[str] = []
    site_locations: list[SiteLocation] = []
    therapeutic_area: str
    condition_mesh_terms: list[str] = []
    brief_summary: str
    detailed_description: str
    arms: list[TrialArm] = []
    interventions: list[Intervention] = []
    eligibility_criteria: list[EligibilityCriteria] = []
    outcome_measures: list[OutcomeMeasure] = []


# ═══════════════════════════════════════════
# PATIENT MODELS
# ═══════════════════════════════════════════

class PatientCondition(BaseModel):
    condition_name: str
    icd10_code: str
    mesh_term: Optional[str] = None
    snomed_code: Optional[str] = None
    onset_date: Optional[date] = None
    resolution_date: Optional[date] = None
    is_ongoing: bool = True
    severity: Severity
    body_system: Optional[str] = None

class PatientMedication(BaseModel):
    medication_name: str
    rxnorm_code: Optional[str] = None
    dose_value: Optional[float] = None
    dose_unit: Optional[str] = None
    route: Optional[str] = None
    frequency: Optional[str] = None
    indication: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_ongoing: bool = True

class AdverseEvent(BaseModel):
    ae_term: str
    meddra_pt: Optional[str] = None
    meddra_soc: Optional[str] = None
    severity: Severity
    serious: bool = False
    causality: Causality
    outcome: str
    onset_date: Optional[date] = None
    resolution_date: Optional[date] = None
    action_taken: Optional[str] = None

class LabResult(BaseModel):
    test_name: str
    loinc_code: Optional[str] = None
    result_value: float
    result_unit: str
    reference_low: Optional[float] = None
    reference_high: Optional[float] = None
    abnormal_flag: Optional[str] = None  # H, L, N
    specimen_type: Optional[str] = None
    collection_date: date
    visit_name: Optional[str] = None

class VitalSign(BaseModel):
    test_name: str  # SYSBP, DIABP, HR, TEMP, WEIGHT
    result_value: float
    result_unit: str
    position: Optional[str] = None
    collection_date: date
    visit_name: Optional[str] = None

class Patient(BaseModel):
    patient_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    subject_id: str
    site_id: Optional[str] = None
    age: int
    sex: Sex
    race: Optional[str] = None
    ethnicity: Optional[str] = None
    country: Optional[str] = None
    enrollment_date: Optional[date] = None
    arm_assigned: Optional[str] = None
    disposition_status: str = "Enrolled"
    conditions: list[PatientCondition] = []
    medications: list[PatientMedication] = []
    adverse_events: list[AdverseEvent] = []
    lab_results: list[LabResult] = []
    vital_signs: list[VitalSign] = []


# ═══════════════════════════════════════════
# COMPLETE TRIAL DOCUMENT (for PDF generation)
# ═══════════════════════════════════════════

class ClinicalTrialDocument(BaseModel):
    """
    A complete clinical trial document that combines
    the trial protocol with enrolled patient data.
    This is what gets serialized into a PDF.
    """
    trial: ClinicalTrial
    patients: list[Patient] = []
    document_type: str = "protocol"  # protocol, summary, patient_report
    generated_at: datetime = Field(default_factory=datetime.utcnow)