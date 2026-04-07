# processor/loaders/postgres_loader.py
import asyncpg
import uuid
import json
import logging
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)


class PostgresLoader:

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(self.dsn, min_size=2, max_size=10)

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def ingest_trial(self, trial_data: dict) -> str:
        """
        Insert or update a clinical trial and all sub-entities.
        Returns the actual trial_id from the database.
        """
        new_trial_id = str(uuid.uuid4())

        async with self.pool.acquire() as conn:
            async with conn.transaction():

                # ════════════════════════════════════════════════════
                # ✅ FIX: Use RETURNING to get the ACTUAL trial_id
                #    If the row already exists, the INSERT becomes
                #    an UPDATE, and the existing trial_id is returned.
                # ════════════════════════════════════════════════════
                row = await conn.fetchrow("""
                    INSERT INTO clinical_trial (
                        trial_id, nct_id, org_study_id, title,
                        official_title, acronym, study_type, phase,
                        allocation, intervention_model, masking,
                        primary_purpose, overall_status, start_date,
                        completion_date, enrollment_count, enrollment_type,
                        lead_sponsor, collaborators, regions, countries,
                        site_locations, therapeutic_area, condition_mesh_terms,
                        source_system
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11, $12, $13, $14, $15, $16, $17, $18, $19,
                        $20, $21, $22, $23, $24, $25
                    )
                    ON CONFLICT (nct_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        official_title = EXCLUDED.official_title,
                        study_type = EXCLUDED.study_type,
                        phase = EXCLUDED.phase,
                        overall_status = EXCLUDED.overall_status,
                        enrollment_count = EXCLUDED.enrollment_count,
                        enrollment_type = EXCLUDED.enrollment_type,
                        lead_sponsor = EXCLUDED.lead_sponsor,
                        therapeutic_area = EXCLUDED.therapeutic_area,
                        updated_at = now(),
                        data_version = clinical_trial.data_version + 1
                    RETURNING trial_id
                """,
                    new_trial_id,
                    trial_data.get('nct_id'),
                    trial_data.get('org_study_id'),
                    trial_data.get('title', ''),
                    trial_data.get('official_title', ''),
                    trial_data.get('acronym'),
                    trial_data.get('study_type', 'Interventional'),
                    trial_data.get('phase', 'N/A'),
                    trial_data.get('allocation'),
                    trial_data.get('intervention_model'),
                    trial_data.get('masking'),
                    trial_data.get('primary_purpose'),
                    trial_data.get('overall_status', 'Unknown'),
                    self._parse_date(trial_data.get('start_date')),
                    self._parse_date(trial_data.get('completion_date')),
                    trial_data.get('enrollment_count', 0),
                    trial_data.get('enrollment_type', 'Anticipated'),
                    trial_data.get('lead_sponsor', ''),
                    trial_data.get('collaborators', []),
                    trial_data.get('regions', []),
                    trial_data.get('countries', []),
                    self._to_jsonb(trial_data.get('site_locations', [])),
                    trial_data.get('therapeutic_area', ''),
                    trial_data.get('condition_mesh_terms', []),
                    'pdf_ingestion'
                )

                # ════════════════════════════════════════════════════
                # ✅ FIX: Use the ACTUAL trial_id from the database
                # ════════════════════════════════════════════════════
                trial_id = str(row['trial_id'])
                logger.info(
                    f"Trial {trial_data.get('nct_id')}: "
                    f"using trial_id={trial_id}"
                )

                # ════════════════════════════════════════════════════
                # ✅ FIX: Delete old child records before re-inserting
                #    This makes the ingestion idempotent — reprocessing
                #    the same PDF replaces old data cleanly.
                # ════════════════════════════════════════════════════
                await conn.execute(
                    "DELETE FROM trial_arm WHERE trial_id = $1", 
                    uuid.UUID(trial_id)
                )
                await conn.execute(
                    "DELETE FROM intervention WHERE trial_id = $1",
                    uuid.UUID(trial_id)
                )
                await conn.execute(
                    "DELETE FROM eligibility_criteria WHERE trial_id = $1",
                    uuid.UUID(trial_id)
                )
                await conn.execute(
                    "DELETE FROM outcome_measure WHERE trial_id = $1",
                    uuid.UUID(trial_id)
                )

                # ── Arms ──
                for arm in trial_data.get('arms', []):
                    await conn.execute("""
                        INSERT INTO trial_arm (
                            arm_id, trial_id, arm_label, arm_type,
                            description, target_enrollment
                        ) VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                        uuid.uuid4(),
                        uuid.UUID(trial_id),
                        arm.get('arm_label', ''),
                        arm.get('arm_type', ''),
                        arm.get('description', ''),
                        arm.get('target_enrollment', 0)
                    )

                # ── Interventions ──
                for interv in trial_data.get('interventions', []):
                    await conn.execute("""
                        INSERT INTO intervention (
                            intervention_id, trial_id, intervention_type,
                            name, generic_name, rxnorm_code,
                            route, frequency, duration, description
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                        uuid.uuid4(),
                        uuid.UUID(trial_id),
                        interv.get('intervention_type', 'Drug'),
                        interv.get('name', ''),
                        interv.get('generic_name'),
                        interv.get('rxnorm_code'),
                        interv.get('route'),
                        interv.get('frequency'),
                        interv.get('duration'),
                        interv.get('description', '')
                    )

                # ── Eligibility ──
                for crit in trial_data.get('eligibility_criteria', []):
                    await conn.execute("""
                        INSERT INTO eligibility_criteria (
                            criteria_id, trial_id, criteria_type,
                            description, min_age, max_age
                        ) VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                        uuid.uuid4(),
                        uuid.UUID(trial_id),
                        crit.get('criteria_type', 'Inclusion'),
                        crit.get('description', ''),
                        crit.get('min_age'),
                        crit.get('max_age')
                    )

                # ── Outcome Measures ──
                for outcome in trial_data.get('outcome_measures', []):
                    await conn.execute("""
                        INSERT INTO outcome_measure (
                            outcome_id, trial_id, outcome_type,
                            measure, time_frame, description
                        ) VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                        uuid.uuid4(),
                        uuid.UUID(trial_id),
                        outcome.get('outcome_type', 'Other'),
                        outcome.get('measure', ''),
                        outcome.get('time_frame', ''),
                        outcome.get('description', '')
                    )

        return trial_id

    async def ingest_patient(
        self,
        patient_data: dict,
        trial_id: str
    ) -> str:
        """
        Insert a patient and all sub-entities.
        Returns patient_id.
        """
        patient_id = str(uuid.uuid4())
        trial_uuid = uuid.UUID(trial_id)

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # ── Patient ──
                await conn.execute("""
                    INSERT INTO patient (
                        patient_id, subject_id, site_id, age, sex,
                        race, ethnicity, country, enrollment_date,
                        arm_assigned, disposition_status
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                    uuid.UUID(patient_id),
                    patient_data.get('subject_id', ''),
                    patient_data.get('site_id'),
                    patient_data.get('age'),
                    patient_data.get('sex', 'U'),
                    patient_data.get('race'),
                    patient_data.get('ethnicity'),
                    patient_data.get('country'),
                    self._parse_date(patient_data.get('enrollment_date')),
                    patient_data.get('arm_assigned'),
                    patient_data.get('disposition_status', 'Enrolled')
                )

                # ── Trial Enrollment ──
                await conn.execute("""
                    INSERT INTO patient_trial_enrollment (
                        enrollment_id, patient_id, trial_id,
                        enrollment_date, status
                    ) VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (patient_id, trial_id) DO NOTHING
                """,
                    uuid.uuid4(),
                    uuid.UUID(patient_id),
                    trial_uuid,
                    self._parse_date(patient_data.get('enrollment_date')),
                    patient_data.get('disposition_status', 'Enrolled')
                )

                # ── Conditions ──
                for cond in patient_data.get('conditions', []):
                    await conn.execute("""
                        INSERT INTO patient_condition (
                            condition_id, patient_id, condition_name,
                            icd10_code, severity, is_ongoing, onset_date
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                        uuid.uuid4(),
                        uuid.UUID(patient_id),
                        cond.get('condition_name', ''),
                        cond.get('icd10_code'),
                        cond.get('severity', 'Unknown'),
                        cond.get('is_ongoing', True),
                        self._parse_date(cond.get('onset_date'))
                    )

                # ── Medications ──
                for med in patient_data.get('medications', []):
                    await conn.execute("""
                        INSERT INTO patient_medication (
                            medication_id, patient_id, medication_name,
                            rxnorm_code, route, frequency, indication
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                        uuid.uuid4(),
                        uuid.UUID(patient_id),
                        med.get('medication_name', ''),
                        med.get('rxnorm_code'),
                        med.get('route'),
                        med.get('frequency'),
                        med.get('indication')
                    )

                # ── Adverse Events ──
                for ae in patient_data.get('adverse_events', []):
                    await conn.execute("""
                        INSERT INTO adverse_event (
                            ae_id, patient_id, trial_id, ae_term,
                            meddra_pt, severity, serious, causality, outcome
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                        uuid.uuid4(),
                        uuid.UUID(patient_id),
                        trial_uuid,
                        ae.get('ae_term', ''),
                        ae.get('meddra_pt'),
                        ae.get('severity', 'Unknown'),
                        ae.get('serious', False),
                        ae.get('causality', 'Unknown'),
                        ae.get('outcome', 'Unknown')
                    )

                # ── Lab Results ──
                for lab in patient_data.get('lab_results', []):
                    result_val = lab.get('result_value')
                    if isinstance(result_val, str):
                        try:
                            result_val = float(result_val)
                        except (ValueError, TypeError):
                            result_val = None
                    await conn.execute("""
                        INSERT INTO lab_result (
                            lab_id, patient_id, trial_id, test_name,
                            loinc_code, result_value, result_unit,
                            abnormal_flag, collection_date, visit_name
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                        uuid.uuid4(),
                        uuid.UUID(patient_id),
                        trial_uuid,
                        lab.get('test_name', ''),
                        lab.get('loinc_code'),
                        result_val,
                        lab.get('result_unit', ''),
                        lab.get('abnormal_flag'),
                        self._parse_date(lab.get('collection_date')),
                        lab.get('visit_name')
                    )

        return patient_id

    def _parse_date(self, val) -> Optional[date]:
        if val is None:
            return None
        if isinstance(val, date):
            return val
        if isinstance(val, datetime):
            return val.date()
        if isinstance(val, str):
            for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%d-%b-%Y'):
                try:
                    return datetime.strptime(val, fmt).date()
                except ValueError:
                    continue
        return None

    def _to_jsonb(self, val):
        if isinstance(val, list):
            return json.dumps([
                v if isinstance(v, dict) else
                v.__dict__ if hasattr(v, '__dict__') else str(v)
                for v in val
            ])
        return json.dumps(val)