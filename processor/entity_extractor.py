# processor/entity_extractor.py
"""
Hybrid entity extraction:
  1. Regex-based extraction for structured fields (NCT IDs, codes, dates)
  2. Table parsing for tabular data
  3. LLM-assisted extraction for unstructured narratives
"""
import re
import logging
from datetime import date
from typing import Optional
from openai import AsyncOpenAI
from processor.pdf_parser import ExtractedDocument, ExtractedSection
from shared.models import (
    ClinicalTrial, Patient, PatientCondition, PatientMedication,
    AdverseEvent, LabResult, Intervention, EligibilityCriteria,
    OutcomeMeasure, TrialArm, SiteLocation
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# KNOWN VALUE SETS — used to detect misclassified rows
# ═══════════════════════════════════════════════════════════════

KNOWN_LAB_TESTS = {
    "white blood cell count", "absolute neutrophil count", "hemoglobin",
    "platelet count", "alt", "ast", "creatinine", "tsh", "bnp",
    "troponin i", "potassium", "egfr", "inr", "hba1c",
    "fasting glucose", "lipase", "sodium", "calcium", "albumin",
    "bilirubin", "ldh", "urea", "cholesterol",
}

KNOWN_LAB_UNITS = {
    "10^3/ul", "g/dl", "u/l", "mg/dl", "meq/l", "ng/ml",
    "pg/ml", "miu/l", "%", "ml/min/1.73m2", "mmol/l",
}

KNOWN_SEVERITIES = {"mild", "moderate", "severe"}

KNOWN_AE_OUTCOMES = {
    "recovered", "ongoing", "fatal", "recovering",
    "not recovered", "recovered with sequelae",
}

KNOWN_CAUSALITIES = {
    "related", "possibly related", "unlikely related", "not related",
}

VALID_SEX_VALUES = {"m", "f", "u", "male", "female", "unknown"}


class ClinicalTrialEntityExtractor:
    """
    Extracts structured entities from parsed PDF sections.
    Combines rule-based and LLM-based extraction.
    """

    def __init__(self, openai_client: AsyncOpenAI, model: str = "gpt-4o"):
        self.llm = openai_client
        self.model = model

    async def extract_all(
        self, doc: ExtractedDocument
    ) -> dict:
        result = {
            "trial": None,
            "patients": [],
            "metadata": doc.metadata
        }

        trial_data = {}

        for section in doc.sections:
            section_type = self._classify_section(section)

            if section_type == 'identification':
                trial_data.update(
                    self._extract_identification(section)
                )
            elif section_type == 'design':
                trial_data.update(
                    self._extract_study_design(section)
                )
            elif section_type == 'arms_interventions':
                arms, interventions = self._extract_arms_interventions(section)
                trial_data['arms'] = arms
                trial_data['interventions'] = interventions
            elif section_type == 'eligibility':
                trial_data['eligibility_criteria'] = (
                    self._extract_eligibility(section)
                )
            elif section_type == 'outcomes':
                trial_data['outcome_measures'] = (
                    self._extract_outcomes(section)
                )
            elif section_type == 'locations':
                trial_data['site_locations'] = (
                    self._extract_locations(section)
                )
            elif section_type == 'overview':
                summaries = await self._extract_overview_with_llm(section)
                trial_data.update(summaries)
            elif section_type == 'patient_detail':
                patient = await self._extract_patient_detail(section)
                if patient and self._validate_patient(patient):
                    result["patients"].append(patient)
                elif patient:
                    logger.warning(
                        f"Rejected invalid patient data: "
                        f"subject_id={patient.get('subject_id')}"
                    )
            elif section_type == 'patient_table':
                patients = self._extract_patient_summary_table(section)
                for p in patients:
                    if self._validate_patient(p):
                        result["patients"].append(p)
                    else:
                        logger.warning(
                            f"Rejected invalid patient row: "
                            f"subject_id={p.get('subject_id')}"
                        )

        trial_data.update({
            k: v for k, v in doc.metadata.items()
            if k not in trial_data
        })

        result["trial"] = trial_data
        logger.info(
            f"Extraction complete: {len(result['patients'])} valid patients"
        )
        return result

    # ═══════════════════════════════════════════════════════════════
    # PATIENT VALIDATION
    # ═══════════════════════════════════════════════════════════════

    def _validate_patient(self, patient: dict) -> bool:
        """
        Validate that extracted data actually represents a patient.
        Rejects rows from condition, lab, medication, or AE tables
        that were misclassified as patients.
        """
        subject_id = (patient.get('subject_id') or '').strip()
        sex = (patient.get('sex') or '').strip().lower()
        age = patient.get('age')
        arm = (patient.get('arm_assigned') or '').strip().lower()
        disposition = (patient.get('disposition_status') or '').strip().lower()

        # ── Rule 1: subject_id must look like a subject ID ──
        # Subject IDs are typically: NCT10000032-0001, SUBJ-001, PT-1234, etc.
        # NOT: "Lipase", "Type 1 Diabetes Mellitus", "Nausea"
        if not subject_id:
            logger.debug("Rejected: empty subject_id")
            return False

        # Subject IDs should contain at least one digit
        if not re.search(r'\d', subject_id):
            logger.debug(f"Rejected: subject_id has no digits: '{subject_id}'")
            return False

        # Subject IDs should NOT be known medical terms
        if subject_id.lower() in KNOWN_LAB_TESTS:
            logger.debug(f"Rejected: subject_id is a lab test: '{subject_id}'")
            return False

        # Subject IDs should be reasonably short (< 50 chars)
        if len(subject_id) > 50:
            logger.debug(f"Rejected: subject_id too long: '{subject_id[:50]}...'")
            return False

        # ── Rule 2: sex must be valid if present ──
        if sex and sex not in VALID_SEX_VALUES:
            # Check if sex looks like a number (lab value leaked)
            try:
                float(sex)
                logger.debug(f"Rejected: sex is numeric: '{sex}'")
                return False
            except ValueError:
                pass

            # Check if sex is a known severity (condition row)
            if sex in KNOWN_SEVERITIES:
                logger.debug(f"Rejected: sex is a severity: '{sex}'")
                return False

        # ── Rule 3: age must be a reasonable integer if present ──
        if age is not None:
            if not isinstance(age, int):
                try:
                    age = int(age)
                except (ValueError, TypeError):
                    logger.debug(f"Rejected: age is not numeric: '{age}'")
                    return False
            if age < 0 or age > 120:
                logger.debug(f"Rejected: age out of range: {age}")
                return False

        # ── Rule 4: arm_assigned should not be a lab unit ──
        if arm in KNOWN_LAB_UNITS:
            logger.debug(f"Rejected: arm_assigned is a lab unit: '{arm}'")
            return False

        # ── Rule 5: disposition should not look like a date range ──
        if disposition and re.match(r'^[\d.]+-[\d.]+$', disposition):
            logger.debug(
                f"Rejected: disposition looks like a reference range: "
                f"'{disposition}'"
            )
            return False

        # ── Rule 6: Must have EITHER age or sex to be a real patient ──
        has_age = age is not None
        has_valid_sex = sex in VALID_SEX_VALUES
        if not has_age and not has_valid_sex:
            logger.debug(
                f"Rejected: no valid age or sex for subject '{subject_id}'"
            )
            return False

        return True

    # ═══════════════════════════════════════════════════════════════
    # TABLE CLASSIFICATION HELPERS
    # ═══════════════════════════════════════════════════════════════

    def _classify_table(self, table: list[list[str]]) -> str:
        """
        Classify a table by examining its headers AND first data row.
        Returns: 'demographics', 'conditions', 'medications',
                 'adverse_events', 'lab_results', 'vital_signs',
                 'patients_summary', 'arms', 'interventions',
                 'outcomes', 'locations', 'key_value', 'unknown'
        """
        if not table or not table[0]:
            return 'unknown'

        headers = [str(h).lower().strip() if h else '' for h in table[0]]
        all_headers = ' '.join(headers)

        # Key-value table (Field | Value)
        if ('field' in headers and 'value' in headers) or \
           (len(headers) == 2 and headers[0] and headers[1]):
            # Check if it looks like key-value by examining data
            if len(table) > 1:
                data_row = table[1]
                if len(data_row) >= 2 and data_row[0]:
                    first_key = str(data_row[0]).lower().strip()
                    if any(k in first_key for k in [
                        'subject', 'age', 'sex', 'race', 'site',
                        'enrollment', 'arm', 'disposition',
                        'name', 'type', 'generic', 'rxnorm',
                        'nct', 'sponsor', 'phase', 'study type'
                    ]):
                        return 'key_value'

        # Patient summary table
        if any('subject' in h for h in headers) and \
           any('age' in h for h in headers):
            return 'patients_summary'

        # Conditions table
        if any('condition' in h for h in headers) or \
           ('icd' in all_headers) or \
           ('severity' in headers and 'ongoing' in all_headers):
            return 'conditions'

        # Medications table
        if any('medication' in h for h in headers) or \
           ('dose' in all_headers and 'route' in all_headers and
            'frequency' in all_headers):
            return 'medications'

        # Adverse events table
        if any('ae' in h or 'adverse' in h for h in headers) or \
           ('causality' in all_headers and 'outcome' in all_headers) or \
           ('meddra' in all_headers) or \
           ('serious' in headers and 'severity' in headers):
            return 'adverse_events'

        # Lab results table
        if any('loinc' in h for h in headers) or \
           any('test' in h for h in headers) and \
           any('value' in h or 'result' in h for h in headers):
            return 'lab_results'

        # Also detect by data content if headers are mangled
        if len(table) > 1:
            first_data = [str(c).lower().strip() if c else '' for c in table[1]]
            first_val = first_data[0] if first_data else ''

            if first_val in KNOWN_LAB_TESTS:
                return 'lab_results'

        # Arms table
        if any('arm' in h or 'label' in h for h in headers):
            return 'arms'

        # Outcomes table
        if any('measure' in h for h in headers) and \
           any('time' in h for h in headers):
            return 'outcomes'

        # Locations table
        if any('facility' in h for h in headers) or \
           ('city' in headers and 'country' in headers):
            return 'locations'

        return 'unknown'

    # ═══════════════════════════════════════════════════════════════
    # SECTION CLASSIFICATION
    # ═══════════════════════════════════════════════════════════════

    def _classify_section(self, section: ExtractedSection) -> str:
        title = section.section_title.upper()
        for pattern, section_type in [
            ('IDENTIFICATION', 'identification'),
            ('OVERVIEW', 'overview'),
            ('DESIGN', 'design'),
            ('ARMS', 'arms_interventions'),
            ('INTERVENTION', 'arms_interventions'),
            ('ELIGIBILITY', 'eligibility'),
            ('OUTCOME', 'outcomes'),
            ('LOCATION', 'locations'),
            ('PATIENT CASE REPORT', 'patient_detail'),
            ('ADDITIONAL ENROLLED', 'patient_table'),
            ('PATIENT DATA', 'patient_summary'),
        ]:
            if pattern in title:
                return section_type
        return 'unknown'

    # ═══════════════════════════════════════════════════════════════
    # EXTRACTION METHODS
    # ═══════════════════════════════════════════════════════════════

    def _extract_identification(self, section: ExtractedSection) -> dict:
        data = {}
        if section.tables:
            for table in section.tables:
                for row in table:
                    if len(row) >= 2 and row[0] and row[1]:
                        key = row[0].strip().lower()
                        value = row[1].strip()
                        if 'nct' in key:
                            data['nct_id'] = value
                        elif 'organization' in key or 'sponsor id' in key:
                            data['org_study_id'] = value
                        elif 'official title' in key:
                            data['official_title'] = value
                        elif 'brief title' in key:
                            data['title'] = value
                        elif 'sponsor' in key:
                            data['lead_sponsor'] = value
                        elif 'collaborator' in key:
                            data['collaborators'] = [
                                c.strip() for c in value.split(',')
                            ]
                        elif 'acronym' in key:
                            data['acronym'] = value
        if 'nct_id' not in data:
            match = re.search(r'NCT\d{8}', section.raw_text)
            if match:
                data['nct_id'] = match.group(0)
        return data

    def _extract_study_design(self, section: ExtractedSection) -> dict:
        data = {}
        field_mapping = {
            'study type': 'study_type',
            'phase': 'phase',
            'allocation': 'allocation',
            'intervention model': 'intervention_model',
            'masking': 'masking',
            'primary purpose': 'primary_purpose',
            'enrollment': 'enrollment_count',
        }
        if section.tables:
            for table in section.tables:
                for row in table:
                    if len(row) >= 2 and row[0] and row[1]:
                        key = row[0].strip().lower()
                        for pattern, field_name in field_mapping.items():
                            if pattern in key:
                                value = row[1].strip()
                                if field_name == 'enrollment_count':
                                    match = re.search(r'(\d+)', value)
                                    if match:
                                        data[field_name] = int(match.group(1))
                                    type_match = re.search(
                                        r'\((Actual|Anticipated)\)', value
                                    )
                                    if type_match:
                                        data['enrollment_type'] = (
                                            type_match.group(1)
                                        )
                                else:
                                    data[field_name] = value
        return data

    def _extract_arms_interventions(
        self, section: ExtractedSection
    ) -> tuple[list[dict], list[dict]]:
        arms = []
        interventions = []

        if section.tables:
            for table in section.tables:
                if not table or not table[0]:
                    continue

                table_type = self._classify_table(table)

                if table_type == 'arms':
                    for row in table[1:]:
                        if row and len(row) >= 3:
                            arms.append({
                                'arm_label': row[0] or '',
                                'arm_type': row[1] or '',
                                'description': row[2] or '',
                                'target_enrollment': (
                                    int(row[3]) if len(row) > 3
                                    and row[3] and str(row[3]).isdigit()
                                    else 0
                                )
                            })
                elif table_type == 'key_value':
                    current_intervention = {}
                    for row in table[1:]:
                        if len(row) >= 2 and row[0] and row[1]:
                            key = row[0].strip().lower()
                            val = row[1].strip() if isinstance(row[1], str) else str(row[1])
                            if 'name' == key:
                                if current_intervention:
                                    interventions.append(current_intervention)
                                current_intervention = {'name': val}
                            elif 'type' in key:
                                current_intervention['intervention_type'] = val
                            elif 'generic' in key:
                                current_intervention['generic_name'] = val
                            elif 'rxnorm' in key:
                                current_intervention['rxnorm_code'] = val
                            elif 'dosage' in key or 'dose' in key:
                                current_intervention['dosage'] = val
                            elif 'route' in key:
                                current_intervention['route'] = val
                            elif 'frequency' in key:
                                current_intervention['frequency'] = val
                            elif 'duration' in key:
                                current_intervention['duration'] = val
                            elif 'description' in key:
                                current_intervention['description'] = val
                    if current_intervention:
                        interventions.append(current_intervention)

        return arms, interventions

    def _extract_eligibility(
        self, section: ExtractedSection
    ) -> list[dict]:
        criteria = []
        current_type = None

        for line in section.raw_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            if 'inclusion' in line.lower():
                current_type = 'Inclusion'
                continue
            elif 'exclusion' in line.lower():
                current_type = 'Exclusion'
                continue
            elif 'demographics' in line.lower():
                age_match = re.search(r'(\d+)\s*to\s*(\d+)', line)
                if age_match:
                    criteria.append({
                        'criteria_type': 'Demographics',
                        'min_age': int(age_match.group(1)),
                        'max_age': int(age_match.group(2)),
                    })
                continue

            crit_match = re.match(r'\s*\d+\.\s*(.+)', line)
            if crit_match and current_type:
                criteria.append({
                    'criteria_type': current_type,
                    'description': crit_match.group(1).strip()
                })

        return criteria

    def _extract_outcomes(
        self, section: ExtractedSection
    ) -> list[dict]:
        outcomes = []
        if section.tables:
            for table in section.tables:
                if not table:
                    continue
                for row in table[1:]:
                    if row and len(row) >= 3:
                        outcomes.append({
                            'outcome_type': row[0] or 'Other',
                            'measure': row[1] or '',
                            'time_frame': row[2] or '',
                            'description': row[3] if len(row) > 3 else ''
                        })
        return outcomes

    def _extract_locations(
        self, section: ExtractedSection
    ) -> list[dict]:
        locations = []

        if section.tables:
            for table in section.tables:
                if not table:
                    continue
                for row in table[1:]:
                    if row and len(row) >= 3:
                        locations.append({
                            'facility': row[0] or '',
                            'city': row[1] or '',
                            'country': row[2] or ''
                        })
        return locations

    async def _extract_overview_with_llm(
        self, section: ExtractedSection
    ) -> dict:
        prompt = f"""Extract the following fields from this clinical trial
overview section. Return as JSON:
{{
    "brief_summary": "...",
    "detailed_description": "...",
    "therapeutic_area": "...",
    "primary_condition": "...",
    "study_drug": "..."
}}

Section text:
{section.raw_text[:3000]}
"""
        response = await self.llm.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You extract structured medical data from clinical trial documents. Return valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0
        )
        import json
        return json.loads(response.choices[0].message.content)

    async def _extract_patient_detail(
        self, section: ExtractedSection
    ) -> Optional[dict]:
        """
        Extract individual patient data from case report section.
        Uses table classification to correctly identify each table type.
        """
        patient = {}

        # Extract subject ID from section title
        id_match = re.search(
            r'PATIENT CASE REPORT:\s*([\w\-]+)',
            section.section_title
        )
        if id_match:
            patient['subject_id'] = id_match.group(1)

        if not section.tables:
            return None

        for table in section.tables:
            if not table or not table[0]:
                continue

            # ═══════════════════════════════════════════
            # ✅ FIX: Use robust table classification
            #    instead of fragile header matching
            # ═══════════════════════════════════════════
            table_type = self._classify_table(table)
            logger.debug(
                f"Patient detail table classified as: {table_type} "
                f"(headers: {table[0][:3]})"
            )

            if table_type == 'key_value':
                # Demographics key-value table
                for row in table[1:]:
                    if len(row) >= 2 and row[0]:
                        key = str(row[0]).strip().lower()
                        val = str(row[1]).strip() if row[1] else ''
                        if 'subject' in key:
                            patient['subject_id'] = val
                        elif 'age' in key and 'unit' not in key:
                            match = re.search(r'(\d+)', val)
                            if match:
                                patient['age'] = int(match.group(1))
                        elif 'sex' in key or 'gender' in key:
                            patient['sex'] = val
                        elif 'race' in key:
                            patient['race'] = val
                        elif 'ethnicity' in key:
                            patient['ethnicity'] = val
                        elif 'country' in key:
                            patient['country'] = val
                        elif 'enrollment' in key:
                            patient['enrollment_date'] = val
                        elif 'arm' in key:
                            patient['arm_assigned'] = val
                        elif 'disposition' in key or 'status' in key:
                            patient['disposition_status'] = val
                        elif 'site' in key:
                            patient['site_id'] = val

            elif table_type == 'conditions':
                patient['conditions'] = []
                for row in table[1:]:
                    if row and len(row) >= 3:
                        patient['conditions'].append({
                            'condition_name': str(row[0] or '').strip(),
                            'icd10_code': str(row[1] or '').strip(),
                            'severity': str(row[2] or '').strip(),
                            'is_ongoing': str(row[3] or '').lower() == 'yes'
                            if len(row) > 3 else True,
                            'onset_date': str(row[4]).strip()
                            if len(row) > 4 and row[4] else None
                        })

            elif table_type == 'medications':
                patient['medications'] = []
                for row in table[1:]:
                    if row and len(row) >= 3:
                        patient['medications'].append({
                            'medication_name': str(row[0] or '').strip(),
                            'dose': str(row[1] or '').strip(),
                            'route': str(row[2] or '').strip(),
                            'frequency': str(row[3]).strip()
                            if len(row) > 3 else '',
                            'indication': str(row[4]).strip()
                            if len(row) > 4 else ''
                        })

            elif table_type == 'adverse_events':
                patient['adverse_events'] = []
                for row in table[1:]:
                    if row and len(row) >= 4:
                        patient['adverse_events'].append({
                            'ae_term': str(row[0] or '').strip(),
                            'meddra_pt': str(row[1] or '').strip(),
                            'severity': str(row[2] or '').strip(),
                            'serious': str(row[3] or '').lower() == 'yes',
                            'causality': str(row[4]).strip()
                            if len(row) > 4 else '',
                            'outcome': str(row[5]).strip()
                            if len(row) > 5 else ''
                        })

            elif table_type == 'lab_results':
                patient['lab_results'] = []
                for row in table[1:]:
                    if row and len(row) >= 4:
                        patient['lab_results'].append({
                            'test_name': str(row[0] or '').strip(),
                            'loinc_code': str(row[1] or '').strip(),
                            'result_value': str(row[2] or '').strip(),
                            'result_unit': str(row[3] or '').strip(),
                            'reference_range': str(row[4]).strip()
                            if len(row) > 4 else '',
                            'abnormal_flag': str(row[5]).strip()
                            if len(row) > 5 else 'N'
                        })

            else:
                # ✅ Skip unknown tables instead of guessing
                logger.debug(
                    f"Skipping unclassified table in patient section "
                    f"(headers: {table[0][:3]})"
                )

        return patient if patient.get('subject_id') else None

    def _extract_patient_summary_table(
        self, section: ExtractedSection
    ) -> list[dict]:
        """
        Extract patient data from the compact summary table.
        Only processes tables that actually look like patient summaries.
        """
        patients = []
        if section.tables:
            for table in section.tables:
                if not table:
                    continue

                # ═══════════════════════════════════════════
                # ✅ FIX: Classify every table — skip non-patient tables
                # ═══════════════════════════════════════════
                table_type = self._classify_table(table)

                if table_type not in ('patients_summary', 'unknown'):
                    # This table is clearly something else (labs, conditions, etc.)
                    # Skip it entirely
                    logger.debug(
                        f"Skipping {table_type} table in patient summary section"
                    )
                    continue

                # ═══════════════════════════════════════════
                # ✅ FIX: Validate the header row matches expected structure
                # ═══════════════════════════════════════════
                if table[0]:
                    headers = [str(h).lower().strip() if h else '' for h in table[0]]
                    # Must have at least subject-like and age-like headers
                    has_subject = any(
                        'subject' in h or 'patient' in h or 'id' in h
                        for h in headers
                    )
                    has_age = any('age' in h for h in headers)
                    has_sex = any('sex' in h or 'gender' in h for h in headers)

                    if not (has_subject or (has_age and has_sex)):
                        logger.debug(
                            f"Table headers don't match patient summary: "
                            f"{headers[:4]}"
                        )
                        continue

                for row in table[1:]:  # Skip header
                    if not row or len(row) < 5:
                        continue

                    # ═══════════════════════════════════════════
                    # ✅ FIX: Validate each row before accepting it
                    # ═══════════════════════════════════════════
                    subject_id = str(row[0] or '').strip()
                    age_str = str(row[1] or '').strip()
                    sex_str = str(row[2] or '').strip().lower()

                    # Quick pre-validation
                    if not subject_id or not re.search(r'\d', subject_id):
                        continue

                    if subject_id.lower() in KNOWN_LAB_TESTS:
                        continue

                    if sex_str and sex_str not in VALID_SEX_VALUES:
                        continue

                    age = None
                    if age_str.isdigit():
                        age = int(age_str)
                        if age < 0 or age > 120:
                            continue

                    patients.append({
                        'subject_id': subject_id,
                        'age': age,
                        'sex': str(row[2] or '').strip(),
                        'arm_assigned': str(row[3] or '').strip(),
                        'disposition_status': str(row[4] or '').strip(),
                        'condition_count': int(row[5])
                        if len(row) > 5 and row[5] and str(row[5]).isdigit()
                        else 0,
                        'ae_count': int(row[6])
                        if len(row) > 6 and row[6] and str(row[6]).isdigit()
                        else 0,
                        'serious_ae_count': int(row[7])
                        if len(row) > 7 and row[7] and str(row[7]).isdigit()
                        else 0,
                    })

        logger.info(
            f"Extracted {len(patients)} valid patients from summary table"
        )
        return patients