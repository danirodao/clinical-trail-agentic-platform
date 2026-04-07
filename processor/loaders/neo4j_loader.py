# ingestion/graph_loader.py
"""
Loads extracted clinical trial data into Neo4j knowledge graph.
Creates nodes and relationships for semantic traversal.
"""
from neo4j import AsyncGraphDatabase
from typing import Optional


class Neo4jGraphLoader:
    """
    Builds the clinical trial knowledge graph in Neo4j.
    Nodes: ClinicalTrial, Patient, Condition, Drug, AdverseEvent, LabTest
    Relationships: ENROLLED_IN, HAS_CONDITION, TAKES_MEDICATION, etc.
    """

    def __init__(self, uri: str, user: str, password: str):
        self.driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async def close(self):
        await self.driver.close()

    async def setup_constraints(self):
        """Create uniqueness constraints and indexes."""
        async with self.driver.session() as session:
            constraints = [
                "CREATE CONSTRAINT IF NOT EXISTS FOR (t:ClinicalTrial) REQUIRE t.trial_id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (t:ClinicalTrial) REQUIRE t.nct_id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Patient) REQUIRE p.patient_id IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Condition) REQUIRE c.icd10_code IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (d:Drug) REQUIRE d.rxnorm_code IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (a:AdverseEvent) REQUIRE a.meddra_pt IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (l:LabTest) REQUIRE l.loinc_code IS UNIQUE",
                # Full-text index for semantic search
                "CREATE FULLTEXT INDEX trial_search IF NOT EXISTS FOR (t:ClinicalTrial) ON EACH [t.title, t.therapeutic_area]",
            ]
            for constraint in constraints:
                await session.run(constraint)

    async def ingest_trial(self, trial_data: dict, trial_id: str):
        """Create trial node and related structure."""
        async with self.driver.session() as session:
            # ── Trial Node ──
            await session.run("""
                MERGE (t:ClinicalTrial {trial_id: $trial_id})
                SET t.nct_id = $nct_id,
                    t.title = $title,
                    t.phase = $phase,
                    t.status = $status,
                    t.therapeutic_area = $therapeutic_area,
                    t.lead_sponsor = $sponsor,
                    t.enrollment_count = $enrollment,
                    t.study_type = $study_type,
                    t.regions = $regions,
                    t.updated_at = datetime()
            """, {
                'trial_id': trial_id,
                'nct_id': trial_data.get('nct_id', ''),
                'title': trial_data.get('title', ''),
                'phase': trial_data.get('phase', ''),
                'status': trial_data.get('overall_status', ''),
                'therapeutic_area': trial_data.get('therapeutic_area', ''),
                'sponsor': trial_data.get('lead_sponsor', ''),
                'enrollment': trial_data.get('enrollment_count', 0),
                'study_type': trial_data.get('study_type', ''),
                'regions': trial_data.get('regions', [])
            })

            # ── Intervention → Drug Nodes + TESTS_INTERVENTION ──
            for interv in trial_data.get('interventions', []):
                if interv.get('rxnorm_code'):
                    await session.run("""
                        MERGE (d:Drug {rxnorm_code: $rxnorm})
                        SET d.name = $name,
                            d.generic_name = $generic,
                            d.type = $type
                        WITH d
                        MATCH (t:ClinicalTrial {trial_id: $trial_id})
                        MERGE (t)-[:TESTS_INTERVENTION {
                            dose: $dose,
                            route: $route,
                            frequency: $frequency
                        }]->(d)
                    """, {
                        'rxnorm': interv['rxnorm_code'],
                        'name': interv.get('name', ''),
                        'generic': interv.get('generic_name', ''),
                        'type': interv.get('intervention_type', 'Drug'),
                        'trial_id': trial_id,
                        'dose': f"{interv.get('dose_value', '')} {interv.get('dose_unit', '')}".strip(),
                        'route': interv.get('route', ''),
                        'frequency': interv.get('frequency', '')
                    })

    async def ingest_patient(
        self,
        patient_data: dict,
        patient_id: str,
        trial_id: str
    ):
        """Create patient node and all relationships."""
        async with self.driver.session() as session:
            # ── Patient Node ──
            await session.run("""
                MERGE (p:Patient {patient_id: $patient_id})
                SET p.subject_id = $subject_id,
                    p.age = $age,
                    p.sex = $sex,
                    p.race = $race,
                    p.country = $country,
                    p.disposition = $disposition
            """, {
                'patient_id': patient_id,
                'subject_id': patient_data.get('subject_id', ''),
                'age': patient_data.get('age'),
                'sex': patient_data.get('sex', 'U'),
                'race': patient_data.get('race'),
                'country': patient_data.get('country'),
                'disposition': patient_data.get('disposition_status', '')
            })

            # ── ENROLLED_IN ──
            await session.run("""
                MATCH (p:Patient {patient_id: $patient_id})
                MATCH (t:ClinicalTrial {trial_id: $trial_id})
                MERGE (p)-[:ENROLLED_IN {
                    arm: $arm,
                    enrollment_date: $date
                }]->(t)
            """, {
                'patient_id': patient_id,
                'trial_id': trial_id,
                'arm': patient_data.get('arm_assigned', ''),
                'date': str(patient_data.get('enrollment_date', ''))
            })

            # ── HAS_CONDITION ──
            for cond in patient_data.get('conditions', []):
                icd10 = cond.get('icd10_code')
                if icd10:
                    await session.run("""
                        MERGE (c:Condition {icd10_code: $icd10})
                        SET c.name = $name,
                            c.mesh_term = $mesh,
                            c.snomed_code = $snomed
                        WITH c
                        MATCH (p:Patient {patient_id: $patient_id})
                        MERGE (p)-[:HAS_CONDITION {
                            severity: $severity,
                            ongoing: $ongoing
                        }]->(c)
                        WITH c
                        MATCH (t:ClinicalTrial {trial_id: $trial_id})
                        MERGE (t)-[:STUDIES]->(c)
                    """, {
                        'icd10': icd10,
                        'name': cond.get('condition_name', ''),
                        'mesh': cond.get('mesh_term'),
                        'snomed': cond.get('snomed_code'),
                        'patient_id': patient_id,
                        'severity': cond.get('severity', ''),
                        'ongoing': cond.get('is_ongoing', True),
                        'trial_id': trial_id
                    })

            # ── TAKES_MEDICATION ──
            for med in patient_data.get('medications', []):
                rxnorm = med.get('rxnorm_code')
                if rxnorm:
                    await session.run("""
                        MERGE (d:Drug {rxnorm_code: $rxnorm})
                        SET d.name = $name
                        WITH d
                        MATCH (p:Patient {patient_id: $patient_id})
                        MERGE (p)-[:TAKES_MEDICATION {
                            dose: $dose,
                            route: $route,
                            frequency: $frequency,
                            indication: $indication
                        }]->(d)
                    """, {
                        'rxnorm': rxnorm,
                        'name': med.get('medication_name', ''),
                        'patient_id': patient_id,
                        'dose': f"{med.get('dose_value', '')} {med.get('dose_unit', '')}".strip(),
                        'route': med.get('route', ''),
                        'frequency': med.get('frequency', ''),
                        'indication': med.get('indication', '')
                    })

            # ── EXPERIENCED (Adverse Events) ──
            for ae in patient_data.get('adverse_events', []):
                meddra = ae.get('meddra_pt')
                if meddra:
                    await session.run("""
                        MERGE (a:AdverseEvent {meddra_pt: $meddra})
                        SET a.term = $term,
                            a.soc = $soc
                        WITH a
                        MATCH (p:Patient {patient_id: $patient_id})
                        MERGE (p)-[:EXPERIENCED {
                            severity: $severity,
                            serious: $serious,
                            causality: $causality,
                            outcome: $outcome
                        }]->(a)
                    """, {
                        'meddra': meddra,
                        'term': ae.get('ae_term', ''),
                        'soc': ae.get('meddra_soc', ''),
                        'patient_id': patient_id,
                        'severity': ae.get('severity', ''),
                        'serious': ae.get('serious', False),
                        'causality': ae.get('causality', ''),
                        'outcome': ae.get('outcome', '')
                    })

                    # Drug → MAY_CAUSE → AE relationship
                    # (Link AE to drugs the patient is taking)
                    for med in patient_data.get('medications', []):
                        if med.get('rxnorm_code'):
                            await session.run("""
                                MATCH (d:Drug {rxnorm_code: $rxnorm})
                                MATCH (a:AdverseEvent {meddra_pt: $meddra})
                                MERGE (d)-[:MAY_CAUSE]->(a)
                            """, {
                                'rxnorm': med['rxnorm_code'],
                                'meddra': meddra
                            })

            # ── Comorbidity relationships ──
            conditions = [
                c.get('icd10_code') for c in patient_data.get('conditions', [])
                if c.get('icd10_code')
            ]
            if len(conditions) > 1:
                for i in range(len(conditions)):
                    for j in range(i + 1, len(conditions)):
                        await session.run("""
                            MATCH (c1:Condition {icd10_code: $code1})
                            MATCH (c2:Condition {icd10_code: $code2})
                            MERGE (c1)-[:COMORBID_WITH]->(c2)
                        """, {
                            'code1': conditions[i],
                            'code2': conditions[j]
                        })