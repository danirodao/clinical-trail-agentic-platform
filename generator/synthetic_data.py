# generator/synthetic_data.py
import random
from datetime import date, datetime, timedelta
from faker import Faker
from shared.models import *

fake = Faker()
Faker.seed(42)


# ═══════════════════════════════════════════
# MEDICAL REFERENCE DATA
# Realistic conditions, drugs, labs, etc.
# ═══════════════════════════════════════════

THERAPEUTIC_AREAS = {
    "Oncology": {
        "conditions": [
            {"name": "Non-Small Cell Lung Cancer", "icd10": "C34.90", "mesh": "D002289", "snomed": "254637007"},
            {"name": "Breast Cancer", "icd10": "C50.919", "mesh": "D001943", "snomed": "254837009"},
            {"name": "Colorectal Cancer", "icd10": "C18.9", "mesh": "D015179", "snomed": "363406005"},
            {"name": "Melanoma", "icd10": "C43.9", "mesh": "D008545", "snomed": "372244006"},
            {"name": "Pancreatic Cancer", "icd10": "C25.9", "mesh": "D010190", "snomed": "363418001"},
        ],
        "drugs": [
            {"name": "Pembrolizumab", "generic": "Pembrolizumab", "rxnorm": "1597876", "type": "Biological",
             "route": "Intravenous", "dose": 200, "unit": "mg", "freq": "Every 3 weeks"},
            {"name": "Nivolumab", "generic": "Nivolumab", "rxnorm": "1597884", "type": "Biological",
             "route": "Intravenous", "dose": 240, "unit": "mg", "freq": "Every 2 weeks"},
            {"name": "Atezolizumab", "generic": "Atezolizumab", "rxnorm": "1876366", "type": "Biological",
             "route": "Intravenous", "dose": 1200, "unit": "mg", "freq": "Every 3 weeks"},
            {"name": "Paclitaxel", "generic": "Paclitaxel", "rxnorm": "56946", "type": "Drug",
             "route": "Intravenous", "dose": 175, "unit": "mg/m2", "freq": "Every 3 weeks"},
        ],
        "adverse_events": [
            {"term": "Nausea", "meddra_pt": "Nausea", "soc": "Gastrointestinal disorders", "serious": False},
            {"term": "Fatigue", "meddra_pt": "Fatigue", "soc": "General disorders", "serious": False},
            {"term": "Neutropenia", "meddra_pt": "Neutropenia", "soc": "Blood disorders", "serious": True},
            {"term": "Immune-mediated hepatitis", "meddra_pt": "Hepatitis", "soc": "Hepatobiliary disorders", "serious": True},
            {"term": "Pneumonitis", "meddra_pt": "Pneumonitis", "soc": "Respiratory disorders", "serious": True},
            {"term": "Rash", "meddra_pt": "Rash", "soc": "Skin disorders", "serious": False},
        ],
        "lab_tests": [
            {"name": "White Blood Cell Count", "loinc": "6690-2", "unit": "10^3/uL", "low": 4.5, "high": 11.0},
            {"name": "Absolute Neutrophil Count", "loinc": "751-8", "unit": "10^3/uL", "low": 1.5, "high": 8.0},
            {"name": "Hemoglobin", "loinc": "718-7", "unit": "g/dL", "low": 12.0, "high": 17.5},
            {"name": "Platelet Count", "loinc": "777-3", "unit": "10^3/uL", "low": 150, "high": 400},
            {"name": "ALT", "loinc": "1742-6", "unit": "U/L", "low": 7, "high": 56},
            {"name": "AST", "loinc": "1920-8", "unit": "U/L", "low": 10, "high": 40},
            {"name": "Creatinine", "loinc": "2160-0", "unit": "mg/dL", "low": 0.7, "high": 1.3},
            {"name": "TSH", "loinc": "3016-3", "unit": "mIU/L", "low": 0.4, "high": 4.0},
        ]
    },
    "Cardiology": {
        "conditions": [
            {"name": "Heart Failure", "icd10": "I50.9", "mesh": "D006333", "snomed": "84114007"},
            {"name": "Atrial Fibrillation", "icd10": "I48.91", "mesh": "D001281", "snomed": "49436004"},
            {"name": "Hypertension", "icd10": "I10", "mesh": "D006973", "snomed": "38341003"},
            {"name": "Acute Coronary Syndrome", "icd10": "I24.9", "mesh": "D054058", "snomed": "394659003"},
        ],
        "drugs": [
            {"name": "Sacubitril/Valsartan", "generic": "Sacubitril/Valsartan", "rxnorm": "1656340", "type": "Drug",
             "route": "Oral", "dose": 97, "unit": "mg", "freq": "Twice daily"},
            {"name": "Empagliflozin", "generic": "Empagliflozin", "rxnorm": "1545653", "type": "Drug",
             "route": "Oral", "dose": 10, "unit": "mg", "freq": "Once daily"},
            {"name": "Apixaban", "generic": "Apixaban", "rxnorm": "1364430", "type": "Drug",
             "route": "Oral", "dose": 5, "unit": "mg", "freq": "Twice daily"},
        ],
        "adverse_events": [
            {"term": "Hypotension", "meddra_pt": "Hypotension", "soc": "Vascular disorders", "serious": True},
            {"term": "Dizziness", "meddra_pt": "Dizziness", "soc": "Nervous system disorders", "serious": False},
            {"term": "Renal impairment", "meddra_pt": "Renal impairment", "soc": "Renal disorders", "serious": True},
            {"term": "Hyperkalemia", "meddra_pt": "Hyperkalaemia", "soc": "Metabolism disorders", "serious": True},
            {"term": "Bleeding event", "meddra_pt": "Haemorrhage", "soc": "Vascular disorders", "serious": True},
        ],
        "lab_tests": [
            {"name": "BNP", "loinc": "42637-9", "unit": "pg/mL", "low": 0, "high": 100},
            {"name": "Troponin I", "loinc": "10839-9", "unit": "ng/mL", "low": 0, "high": 0.04},
            {"name": "Potassium", "loinc": "2823-3", "unit": "mEq/L", "low": 3.5, "high": 5.0},
            {"name": "Creatinine", "loinc": "2160-0", "unit": "mg/dL", "low": 0.7, "high": 1.3},
            {"name": "eGFR", "loinc": "33914-3", "unit": "mL/min/1.73m2", "low": 60, "high": 120},
            {"name": "INR", "loinc": "6301-6", "unit": "", "low": 0.8, "high": 1.2},
        ]
    },
    "Endocrinology": {
        "conditions": [
            {"name": "Type 2 Diabetes Mellitus", "icd10": "E11.9", "mesh": "D003924", "snomed": "44054006"},
            {"name": "Type 1 Diabetes Mellitus", "icd10": "E10.9", "mesh": "D003922", "snomed": "46635009"},
            {"name": "Obesity", "icd10": "E66.01", "mesh": "D009765", "snomed": "414916001"},
            {"name": "Diabetic Nephropathy", "icd10": "E11.21", "mesh": "D003928", "snomed": "127013003"},
        ],
        "drugs": [
            {"name": "Semaglutide", "generic": "Semaglutide", "rxnorm": "1991302", "type": "Biological",
             "route": "Subcutaneous", "dose": 1.0, "unit": "mg", "freq": "Once weekly"},
            {"name": "Tirzepatide", "generic": "Tirzepatide", "rxnorm": "2601734", "type": "Biological",
             "route": "Subcutaneous", "dose": 5, "unit": "mg", "freq": "Once weekly"},
            {"name": "Metformin", "generic": "Metformin", "rxnorm": "6809", "type": "Drug",
             "route": "Oral", "dose": 1000, "unit": "mg", "freq": "Twice daily"},
            {"name": "Empagliflozin", "generic": "Empagliflozin", "rxnorm": "1545653", "type": "Drug",
             "route": "Oral", "dose": 10, "unit": "mg", "freq": "Once daily"},
        ],
        "adverse_events": [
            {"term": "Nausea", "meddra_pt": "Nausea", "soc": "Gastrointestinal disorders", "serious": False},
            {"term": "Hypoglycemia", "meddra_pt": "Hypoglycaemia", "soc": "Metabolism disorders", "serious": True},
            {"term": "Injection site reaction", "meddra_pt": "Injection site reaction", "soc": "General disorders", "serious": False},
            {"term": "Pancreatitis", "meddra_pt": "Pancreatitis", "soc": "Gastrointestinal disorders", "serious": True},
            {"term": "Diarrhea", "meddra_pt": "Diarrhoea", "soc": "Gastrointestinal disorders", "serious": False},
        ],
        "lab_tests": [
            {"name": "HbA1c", "loinc": "4548-4", "unit": "%", "low": 4.0, "high": 5.6},
            {"name": "Fasting Glucose", "loinc": "1558-6", "unit": "mg/dL", "low": 70, "high": 100},
            {"name": "Creatinine", "loinc": "2160-0", "unit": "mg/dL", "low": 0.7, "high": 1.3},
            {"name": "eGFR", "loinc": "33914-3", "unit": "mL/min/1.73m2", "low": 60, "high": 120},
            {"name": "Lipase", "loinc": "3040-3", "unit": "U/L", "low": 0, "high": 160},
        ]
    }
}

REGIONS_COUNTRIES = {
    "North America": ["United States", "Canada", "Mexico"],
    "Europe": ["United Kingdom", "Germany", "France", "Spain", "Italy", "Netherlands"],
    "Asia-Pacific": ["Japan", "South Korea", "Australia", "China", "India"],
    "Latin America": ["Brazil", "Argentina", "Colombia", "Chile"],
}

RACE_OPTIONS = ["White", "Black or African American", "Asian",
                "American Indian or Alaska Native", "Native Hawaiian or Other Pacific Islander", "Other", "Unknown"]
ETHNICITY_OPTIONS = ["Hispanic or Latino", "Not Hispanic or Latino", "Unknown"]
MASKING_OPTIONS = ["None (Open Label)", "Single (Participant)", "Double (Participant, Investigator)",
                   "Triple (Participant, Investigator, Outcomes Assessor)", "Quadruple"]
ALLOCATION_OPTIONS = ["Randomized", "Non-Randomized", "N/A"]
INTERVENTION_MODELS = ["Parallel Assignment", "Crossover Assignment", "Sequential Assignment",
                       "Single Group Assignment", "Factorial Assignment"]
PRIMARY_PURPOSES = ["Treatment", "Prevention", "Diagnostic", "Supportive Care",
                    "Screening", "Health Services Research", "Basic Science"]


# ═══════════════════════════════════════════
# GENERATOR CLASS
# ═══════════════════════════════════════════

class ClinicalTrialGenerator:
    """
    Generates realistic synthetic clinical trial data
    for PDF generation and testing.
    """

    def __init__(self, seed: int = 42):
        self.fake = Faker()
        Faker.seed(seed)
        random.seed(seed)
        self._nct_counter = 10000000

    def generate_nct_id(self) -> str:
        self._nct_counter += random.randint(1, 100)
        return f"NCT{self._nct_counter:08d}"

    def generate_trial(
        self,
        therapeutic_area: str | None = None,
        num_patients: int = 20,
        num_arms: int = 2
    ) -> ClinicalTrialDocument:
        """
        Generate a complete clinical trial document with
        protocol details and patient data.
        """
        if therapeutic_area is None:
            therapeutic_area = random.choice(list(THERAPEUTIC_AREAS.keys()))

        ta_data = THERAPEUTIC_AREAS[therapeutic_area]
        primary_condition = random.choice(ta_data["conditions"])
        study_drug = random.choice(ta_data["drugs"])

        # ── Trial Protocol ──
        nct_id = self.generate_nct_id()
        start_date = self.fake.date_between(start_date="-3y", end_date="-6m")
        completion_date = start_date + timedelta(days=random.randint(180, 1095))

        selected_regions = random.sample(
            list(REGIONS_COUNTRIES.keys()),
            k=random.randint(1, 3)
        )
        selected_countries = []
        site_locations = []
        for region in selected_regions:
            countries = random.sample(
                REGIONS_COUNTRIES[region],
                k=random.randint(1, min(3, len(REGIONS_COUNTRIES[region])))
            )
            selected_countries.extend(countries)
            for country in countries:
                site_locations.append(SiteLocation(
                    facility=f"{self.fake.company()} Medical Center",
                    city=self.fake.city(),
                    country=country,
                    latitude=float(self.fake.latitude()),
                    longitude=float(self.fake.longitude())
                ))

        phase = random.choice(list(StudyPhase))
        status = random.choice(list(OverallStatus))

        # ── Arms ──
        arms = []
        arms.append(TrialArm(
            arm_label=f"{study_drug['name']} Treatment Arm",
            arm_type=ArmType.EXPERIMENTAL,
            description=f"{study_drug['name']} {study_drug['dose']} {study_drug['unit']} "
                        f"{study_drug['route']} {study_drug['freq']}",
            target_enrollment=num_patients // num_arms
        ))
        if num_arms >= 2:
            arms.append(TrialArm(
                arm_label="Placebo Arm",
                arm_type=ArmType.PLACEBO_COMPARATOR,
                description=f"Matching placebo {study_drug['route']} {study_drug['freq']}",
                target_enrollment=num_patients // num_arms
            ))
        if num_arms >= 3:
            comparator = random.choice(
                [d for d in ta_data["drugs"] if d["name"] != study_drug["name"]]
            ) if len(ta_data["drugs"]) > 1 else study_drug
            arms.append(TrialArm(
                arm_label=f"{comparator['name']} Active Comparator Arm",
                arm_type=ArmType.ACTIVE_COMPARATOR,
                description=f"{comparator['name']} {comparator['dose']} {comparator['unit']} "
                            f"{comparator['route']} {comparator['freq']}",
                target_enrollment=num_patients // num_arms
            ))

        # ── Interventions ──
        interventions = []
        interventions.append(Intervention(
            intervention_type=InterventionType(study_drug["type"]),
            name=study_drug["name"],
            generic_name=study_drug["generic"],
            rxnorm_code=study_drug["rxnorm"],
            dosage_form=f"{study_drug['route']} formulation",
            dose_value=study_drug["dose"],
            dose_unit=study_drug["unit"],
            route=study_drug["route"],
            frequency=study_drug["freq"],
            duration=f"{random.choice([12, 24, 36, 52])} weeks",
            description=f"{study_drug['name']} administered {study_drug['route'].lower()} "
                        f"at {study_drug['dose']} {study_drug['unit']} {study_drug['freq'].lower()}"
        ))

        # ── Eligibility ──
        min_age = random.choice([18, 21, 40, 50])
        max_age = random.choice([65, 70, 75, 80, 85])
        eligibility = [
            EligibilityCriteria(
                criteria_type="Inclusion",
                description=f"Confirmed diagnosis of {primary_condition['name']}",
                min_age=min_age, max_age=max_age, gender="All"
            ),
            EligibilityCriteria(
                criteria_type="Inclusion",
                description=f"Age ≥ {min_age} and ≤ {max_age} years",
                min_age=min_age, max_age=max_age
            ),
            EligibilityCriteria(
                criteria_type="Inclusion",
                description="Eastern Cooperative Oncology Group (ECOG) performance status 0-1"
                if therapeutic_area == "Oncology" else "Adequate organ function as defined by protocol",
            ),
            EligibilityCriteria(
                criteria_type="Inclusion",
                description="Signed informed consent",
            ),
            EligibilityCriteria(
                criteria_type="Exclusion",
                description="Known hypersensitivity to study drug or any component",
            ),
            EligibilityCriteria(
                criteria_type="Exclusion",
                description="Pregnant or breastfeeding women",
            ),
            EligibilityCriteria(
                criteria_type="Exclusion",
                description="Active autoimmune disease requiring systemic treatment "
                            "within the past 2 years" if therapeutic_area == "Oncology"
                else "Severe hepatic impairment (Child-Pugh C)",
            ),
            EligibilityCriteria(
                criteria_type="Exclusion",
                description=f"Participation in another clinical trial within {random.choice([28, 30, 42])} days",
            ),
        ]

        # ── Outcomes ──
        outcomes = self._generate_outcomes(
            therapeutic_area, primary_condition, study_drug
        )

        trial = ClinicalTrial(
            nct_id=nct_id,
            org_study_id=f"{self.fake.bothify('???-####').upper()}",
            title=f"A {phase.value} Study of {study_drug['name']} in Patients "
                  f"with {primary_condition['name']}",
            official_title=f"A {random.choice(MASKING_OPTIONS).split('(')[0].strip()}, "
                          f"{random.choice(ALLOCATION_OPTIONS)}, {phase.value} Study to Evaluate "
                          f"the Efficacy and Safety of {study_drug['name']} in Patients with "
                          f"{primary_condition['name']}",
            study_type=StudyType.INTERVENTIONAL,
            phase=phase,
            allocation=random.choice(ALLOCATION_OPTIONS),
            intervention_model=random.choice(INTERVENTION_MODELS),
            masking=random.choice(MASKING_OPTIONS),
            primary_purpose=random.choice(PRIMARY_PURPOSES[:3]),
            overall_status=status,
            start_date=start_date,
            completion_date=completion_date if status == OverallStatus.COMPLETED else None,
            last_update_date=datetime.utcnow(),
            enrollment_count=num_patients,
            enrollment_type="Actual" if status == OverallStatus.COMPLETED else "Anticipated",
            lead_sponsor=f"{self.fake.company()} Pharmaceuticals",
            collaborators=[f"{self.fake.company()} Research Institute"
                          for _ in range(random.randint(0, 3))],
            regions=selected_regions,
            countries=selected_countries,
            site_locations=site_locations,
            therapeutic_area=therapeutic_area,
            condition_mesh_terms=[primary_condition["mesh"]],
            brief_summary=self._generate_brief_summary(
                study_drug, primary_condition, phase
            ),
            detailed_description=self._generate_detailed_description(
                study_drug, primary_condition, phase, arms
            ),
            arms=arms,
            interventions=interventions,
            eligibility_criteria=eligibility,
            outcome_measures=outcomes
        )

        # ── Patients ──
        patients = [
            self._generate_patient(
                trial=trial,
                ta_data=ta_data,
                primary_condition=primary_condition,
                patient_index=i
            )
            for i in range(num_patients)
        ]

        return ClinicalTrialDocument(
            trial=trial,
            patients=patients,
            document_type="protocol"
        )

    def _generate_patient(
        self,
        trial: ClinicalTrial,
        ta_data: dict,
        primary_condition: dict,
        patient_index: int
    ) -> Patient:
        """Generate a single realistic patient record."""
        sex = random.choice([Sex.MALE, Sex.FEMALE])
        min_age = trial.eligibility_criteria[1].min_age or 18
        max_age = trial.eligibility_criteria[1].max_age or 85
        age = random.randint(min_age, max_age)

        # Assign to an arm
        arm = random.choice(trial.arms)
        enrollment_date = trial.start_date + timedelta(
            days=random.randint(0, 90)
        )

        # Primary condition (all patients have this)
        conditions = [PatientCondition(
            condition_name=primary_condition["name"],
            icd10_code=primary_condition["icd10"],
            mesh_term=primary_condition["mesh"],
            snomed_code=primary_condition["snomed"],
            onset_date=enrollment_date - timedelta(days=random.randint(30, 3650)),
            is_ongoing=True,
            severity=random.choice(list(Severity))
        )]

        # Add 0-3 comorbidities
        other_conditions = [c for c in ta_data["conditions"]
                          if c["name"] != primary_condition["name"]]
        for cond in random.sample(
            other_conditions,
            k=min(random.randint(0, 3), len(other_conditions))
        ):
            conditions.append(PatientCondition(
                condition_name=cond["name"],
                icd10_code=cond["icd10"],
                mesh_term=cond["mesh"],
                snomed_code=cond["snomed"],
                onset_date=enrollment_date - timedelta(days=random.randint(30, 3650)),
                is_ongoing=random.choice([True, True, False]),
                severity=random.choice(list(Severity))
            ))

        # Medications (1-4)
        medications = []
        for drug in random.sample(
            ta_data["drugs"],
            k=random.randint(1, min(4, len(ta_data["drugs"])))
        ):
            medications.append(PatientMedication(
                medication_name=drug["name"],
                rxnorm_code=drug["rxnorm"],
                dose_value=drug["dose"],
                dose_unit=drug["unit"],
                route=drug["route"],
                frequency=drug["freq"],
                indication=primary_condition["name"],
                start_date=enrollment_date,
                is_ongoing=True
            ))

        # Adverse Events (0-5)
        adverse_events = []
        for ae in random.sample(
            ta_data["adverse_events"],
            k=random.randint(0, min(5, len(ta_data["adverse_events"])))
        ):
            onset = enrollment_date + timedelta(days=random.randint(1, 180))
            resolved = random.choice([True, True, False])
            adverse_events.append(AdverseEvent(
                ae_term=ae["term"],
                meddra_pt=ae["meddra_pt"],
                meddra_soc=ae["soc"],
                severity=random.choice(list(Severity)),
                serious=ae["serious"],
                causality=random.choice(list(Causality)),
                outcome="Recovered" if resolved else "Ongoing",
                onset_date=onset,
                resolution_date=onset + timedelta(days=random.randint(1, 30)) if resolved else None,
                action_taken=random.choice([
                    "None", "Dose Reduced", "Drug Interrupted",
                    "Drug Withdrawn", "Concomitant Medication Given"
                ])
            ))

        # Lab Results (multiple visits)
        lab_results = []
        visit_names = ["Screening", "Baseline", "Week 4", "Week 8",
                       "Week 12", "Week 24", "Week 36", "Week 52"]
        for visit_idx, visit in enumerate(
            visit_names[:random.randint(3, len(visit_names))]
        ):
            visit_date = enrollment_date + timedelta(
                days=visit_idx * random.randint(21, 35)
            )
            for lab in ta_data["lab_tests"]:
                # Generate realistic values with some abnormals
                normal_mean = (lab["low"] + lab["high"]) / 2
                normal_std = (lab["high"] - lab["low"]) / 4
                value = round(random.gauss(normal_mean, normal_std), 2)

                # 15% chance of abnormal value
                if random.random() < 0.15:
                    value = round(
                        random.uniform(lab["low"] * 0.5, lab["high"] * 1.5), 2
                    )

                flag = "N"
                if value < lab["low"]:
                    flag = "L"
                elif value > lab["high"]:
                    flag = "H"

                lab_results.append(LabResult(
                    test_name=lab["name"],
                    loinc_code=lab["loinc"],
                    result_value=value,
                    result_unit=lab["unit"],
                    reference_low=lab["low"],
                    reference_high=lab["high"],
                    abnormal_flag=flag,
                    specimen_type="Blood",
                    collection_date=visit_date,
                    visit_name=visit
                ))

        # Vital Signs
        vital_signs = []
        for visit_idx, visit in enumerate(
            visit_names[:random.randint(3, 6)]
        ):
            visit_date = enrollment_date + timedelta(
                days=visit_idx * random.randint(21, 35)
            )
            vital_signs.extend([
                VitalSign(test_name="SYSBP", result_value=round(random.gauss(125, 15), 1),
                         result_unit="mmHg", position="SITTING", collection_date=visit_date,
                         visit_name=visit),
                VitalSign(test_name="DIABP", result_value=round(random.gauss(78, 10), 1),
                         result_unit="mmHg", position="SITTING", collection_date=visit_date,
                         visit_name=visit),
                VitalSign(test_name="HR", result_value=round(random.gauss(72, 12), 1),
                         result_unit="bpm", position="SITTING", collection_date=visit_date,
                         visit_name=visit),
                VitalSign(test_name="TEMP", result_value=round(random.gauss(36.8, 0.4), 1),
                         result_unit="°C", collection_date=visit_date, visit_name=visit),
                VitalSign(test_name="WEIGHT", result_value=round(random.gauss(78, 15), 1),
                         result_unit="kg", collection_date=visit_date, visit_name=visit),
            ])

        disposition = random.choices(
            ["Completed", "Enrolled", "Withdrawn", "Screen Failure"],
            weights=[0.6, 0.2, 0.15, 0.05]
        )[0]

        return Patient(
            subject_id=f"{trial.nct_id}-{patient_index + 1:04d}",
            site_id=random.choice(trial.site_locations).facility
            if trial.site_locations else "Site-001",
            age=age,
            sex=sex,
            race=random.choice(RACE_OPTIONS),
            ethnicity=random.choice(ETHNICITY_OPTIONS),
            country=random.choice(trial.countries) if trial.countries else "US",
            enrollment_date=enrollment_date,
            arm_assigned=arm.arm_label,
            disposition_status=disposition,
            conditions=conditions,
            medications=medications,
            adverse_events=adverse_events,
            lab_results=lab_results,
            vital_signs=vital_signs
        )

    def _generate_brief_summary(self, drug, condition, phase) -> str:
        return (
            f"This {phase.value} clinical trial evaluates the efficacy and safety of "
            f"{drug['name']} for the treatment of {condition['name']}. "
            f"The study aims to assess whether {drug['name']}, administered "
            f"{drug['route'].lower()} at {drug['dose']} {drug['unit']} "
            f"{drug['freq'].lower()}, demonstrates superior clinical outcomes "
            f"compared to placebo in patients diagnosed with {condition['name']}. "
            f"Primary endpoints include overall response rate and progression-free "
            f"survival. Safety and tolerability will be monitored throughout the study "
            f"with regular assessments of adverse events, laboratory parameters, "
            f"and vital signs."
        )

    def _generate_detailed_description(self, drug, condition, phase, arms) -> str:
        arm_desc = "; ".join([f"{a.arm_label} ({a.description})" for a in arms])
        return (
            f"{condition['name']} remains a significant clinical challenge with "
            f"limited treatment options. Preclinical and early clinical data suggest "
            f"that {drug['name']} may provide meaningful clinical benefit through "
            f"its novel mechanism of action. "
            f"\n\nThis {phase.value} study employs a multi-center, randomized design "
            f"with the following arms: {arm_desc}. "
            f"\n\nPatients will be assessed at regular intervals using standardized "
            f"clinical assessments, laboratory monitoring, and patient-reported "
            f"outcomes. The study includes a screening period of up to 28 days, "
            f"a treatment period, and a follow-up period of 30 days after the "
            f"last dose."
        )

    def _generate_outcomes(self, therapeutic_area, condition, drug) -> list[OutcomeMeasure]:
        outcomes = []
        if therapeutic_area == "Oncology":
            outcomes = [
                OutcomeMeasure(outcome_type="Primary", measure="Overall Response Rate (ORR)",
                              time_frame="From randomization to end of treatment (up to 52 weeks)",
                              description="Proportion of patients with complete or partial response per RECIST 1.1"),
                OutcomeMeasure(outcome_type="Primary", measure="Progression-Free Survival (PFS)",
                              time_frame="From randomization to disease progression or death (up to 36 months)",
                              description="Time from randomization to first documented disease progression or death"),
                OutcomeMeasure(outcome_type="Secondary", measure="Overall Survival (OS)",
                              time_frame="From randomization to death from any cause (up to 60 months)",
                              description="Time from randomization to death from any cause"),
                OutcomeMeasure(outcome_type="Secondary", measure="Duration of Response (DoR)",
                              time_frame="From first response to progression (up to 36 months)",
                              description="Time from first documented response to disease progression"),
            ]
        elif therapeutic_area == "Cardiology":
            outcomes = [
                OutcomeMeasure(outcome_type="Primary", measure="Cardiovascular Death or Heart Failure Hospitalization",
                              time_frame="From randomization to event (up to 36 months)",
                              description="Composite of cardiovascular death or first hospitalization for heart failure"),
                OutcomeMeasure(outcome_type="Secondary", measure="Change in NT-proBNP",
                              time_frame="Baseline to Week 12",
                              description="Change from baseline in NT-proBNP levels"),
            ]
        elif therapeutic_area == "Endocrinology":
            outcomes = [
                OutcomeMeasure(outcome_type="Primary", measure="Change in HbA1c",
                              time_frame="Baseline to Week 26",
                              description=f"Change from baseline in HbA1c at Week 26 for {drug['name']} vs placebo"),
                OutcomeMeasure(outcome_type="Secondary", measure="Percentage of patients achieving HbA1c < 7%",
                              time_frame="At Week 26",
                              description="Proportion of patients reaching target HbA1c < 7%"),
                OutcomeMeasure(outcome_type="Secondary", measure="Change in body weight",
                              time_frame="Baseline to Week 26",
                              description="Percent change from baseline in body weight"),
            ]
        # Common safety outcome
        outcomes.append(OutcomeMeasure(
            outcome_type="Secondary",
            measure="Incidence of Treatment-Emergent Adverse Events",
            time_frame="From first dose to 30 days after last dose",
            description="Number and percentage of patients experiencing adverse events"
        ))
        return outcomes

    def generate_batch(
        self,
        num_trials: int = 10,
        patients_per_trial: int = 20
    ) -> list[ClinicalTrialDocument]:
        """Generate a batch of trials across all therapeutic areas."""
        docs = []
        areas = list(THERAPEUTIC_AREAS.keys())
        for i in range(num_trials):
            area = areas[i % len(areas)]
            num_patients = random.randint(
                max(10, patients_per_trial - 10),
                patients_per_trial + 10
            )
            docs.append(self.generate_trial(
                therapeutic_area=area,
                num_patients=num_patients,
                num_arms=random.choice([2, 2, 3])
            ))
        return docs