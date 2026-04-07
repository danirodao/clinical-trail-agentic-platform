# generator/pdf_builder.py
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether, HRFlowable
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from datetime import datetime
from shared.models import *
import os


class ClinicalTrialPDFBuilder:
    """
    Generates realistic clinical trial protocol PDFs
    that resemble actual ClinicalTrials.gov documents.
    """

    def __init__(self, output_dir: str = "./generated_pdfs"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

    def _setup_custom_styles(self):
        """Define custom paragraph styles for clinical documents."""
        self.styles.add(ParagraphStyle(
            name='DocTitle',
            parent=self.styles['Heading1'],
            fontSize=16,
            textColor=colors.HexColor('#1a365d'),
            spaceAfter=12,
            alignment=TA_CENTER
        ))
        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Heading2'],
            fontSize=13,
            textColor=colors.HexColor('#2c5282'),
            spaceBefore=16,
            spaceAfter=8,
            borderWidth=1,
            borderColor=colors.HexColor('#2c5282'),
            borderPadding=4
        ))
        self.styles.add(ParagraphStyle(
            name='SubSection',
            parent=self.styles['Heading3'],
            fontSize=11,
            textColor=colors.HexColor('#2d3748'),
            spaceBefore=10,
            spaceAfter=6
        ))
        self.styles.add(ParagraphStyle(
            name='BodyText_Custom',
            parent=self.styles['BodyText'],
            fontSize=9,
            leading=13,
            alignment=TA_JUSTIFY,
            spaceAfter=6
        ))
        self.styles.add(ParagraphStyle(
            name='TableCell',
            parent=self.styles['BodyText'],
            fontSize=8,
            leading=10
        ))
        self.styles.add(ParagraphStyle(
            name='MetaData',
            parent=self.styles['BodyText'],
            fontSize=9,
            textColor=colors.HexColor('#4a5568')
        ))

    def build_trial_protocol_pdf(
        self,
        doc: ClinicalTrialDocument
    ) -> str:
        """
        Generate a complete clinical trial protocol PDF.
        Returns the file path of the generated PDF.
        """
        trial = doc.trial
        filename = f"{trial.nct_id}_protocol.pdf"
        filepath = os.path.join(self.output_dir, filename)

        pdf = SimpleDocTemplate(
            filepath,
            pagesize=letter,
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch
        )

        story = []

        # ── Title Page ──
        story.extend(self._build_title_page(trial))
        story.append(PageBreak())

        # ── Study Identification ──
        story.extend(self._build_identification_section(trial))

        # ── Study Overview ──
        story.extend(self._build_overview_section(trial))

        # ── Study Design ──
        story.extend(self._build_design_section(trial))

        # ── Arms & Interventions ──
        story.extend(self._build_arms_section(trial))

        # ── Eligibility Criteria ──
        story.extend(self._build_eligibility_section(trial))

        # ── Outcome Measures ──
        story.extend(self._build_outcomes_section(trial))

        # ── Site Locations ──
        story.extend(self._build_locations_section(trial))

        # ── Patient Data Section ──
        if doc.patients:
            story.append(PageBreak())
            story.extend(self._build_patient_summary_section(doc.patients, trial))

            # Individual patient details (first 5 patients as examples)
            for patient in doc.patients[:5]:
                story.append(PageBreak())
                story.extend(self._build_patient_detail_section(patient, trial))

            # Remaining patients as summary table
            if len(doc.patients) > 5:
                story.append(PageBreak())
                story.extend(
                    self._build_remaining_patients_table(doc.patients[5:])
                )

        pdf.build(story)
        return filepath

    def _build_title_page(self, trial: ClinicalTrial) -> list:
        elements = []
        elements.append(Spacer(1, 2 * inch))
        elements.append(Paragraph("CLINICAL TRIAL PROTOCOL", self.styles['DocTitle']))
        elements.append(Spacer(1, 0.3 * inch))
        elements.append(HRFlowable(
            width="80%", thickness=2,
            color=colors.HexColor('#2c5282')
        ))
        elements.append(Spacer(1, 0.3 * inch))
        elements.append(Paragraph(trial.official_title, self.styles['Heading2']))
        elements.append(Spacer(1, 0.5 * inch))

        meta_data = [
            ["ClinicalTrials.gov ID:", trial.nct_id],
            ["Sponsor Study ID:", trial.org_study_id or "N/A"],
            ["Sponsor:", trial.lead_sponsor],
            ["Phase:", trial.phase.value],
            ["Status:", trial.overall_status.value],
            ["Therapeutic Area:", trial.therapeutic_area],
            ["Study Type:", trial.study_type.value],
            ["Start Date:", str(trial.start_date)],
            ["Enrollment:", f"{trial.enrollment_count} ({trial.enrollment_type})"],
        ]

        meta_table = Table(meta_data, colWidths=[2.5 * inch, 4 * inch])
        meta_table.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#2d3748')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(meta_table)

        elements.append(Spacer(1, 1 * inch))
        elements.append(Paragraph(
            f"Document Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            self.styles['MetaData']
        ))
        elements.append(Paragraph(
            "CONFIDENTIAL — For Authorized Research Personnel Only",
            ParagraphStyle(
                'Confidential', parent=self.styles['BodyText'],
                fontSize=10, textColor=colors.red, alignment=TA_CENTER
            )
        ))
        return elements

    def _build_identification_section(self, trial: ClinicalTrial) -> list:
        elements = []
        elements.append(Paragraph("1. STUDY IDENTIFICATION", self.styles['SectionHeader']))

        id_data = [
            ["Field", "Value"],
            ["NCT Number", trial.nct_id],
            ["Organization Study ID", trial.org_study_id or "N/A"],
            ["Brief Title", trial.title],
            ["Official Title", trial.official_title],
            ["Acronym", trial.acronym or "N/A"],
            ["Lead Sponsor", trial.lead_sponsor],
            ["Collaborators", ", ".join(trial.collaborators) if trial.collaborators else "None"],
        ]

        table = Table(id_data, colWidths=[2 * inch, 4.5 * inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c5282')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('PADDING', (0, 0), (-1, -1), 6),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f7fafc')]),
        ]))
        elements.append(table)
        return elements

    def _build_overview_section(self, trial: ClinicalTrial) -> list:
        elements = []
        elements.append(Paragraph("2. STUDY OVERVIEW", self.styles['SectionHeader']))
        elements.append(Paragraph("2.1 Brief Summary", self.styles['SubSection']))
        elements.append(Paragraph(trial.brief_summary, self.styles['BodyText_Custom']))
        elements.append(Paragraph("2.2 Detailed Description", self.styles['SubSection']))
        # Handle newlines in detailed description
        for paragraph in trial.detailed_description.split('\n\n'):
            if paragraph.strip():
                elements.append(Paragraph(
                    paragraph.strip(), self.styles['BodyText_Custom']
                ))
        return elements

    def _build_design_section(self, trial: ClinicalTrial) -> list:
        elements = []
        elements.append(Paragraph("3. STUDY DESIGN", self.styles['SectionHeader']))

        design_data = [
            ["Parameter", "Value"],
            ["Study Type", trial.study_type.value],
            ["Phase", trial.phase.value],
            ["Allocation", trial.allocation or "N/A"],
            ["Intervention Model", trial.intervention_model or "N/A"],
            ["Masking", trial.masking or "N/A"],
            ["Primary Purpose", trial.primary_purpose or "N/A"],
            ["Enrollment", f"{trial.enrollment_count} ({trial.enrollment_type})"],
        ]

        table = Table(design_data, colWidths=[2.5 * inch, 4 * inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c5282')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('PADDING', (0, 0), (-1, -1), 6),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f7fafc')]),
        ]))
        elements.append(table)
        return elements

    def _build_arms_section(self, trial: ClinicalTrial) -> list:
        elements = []
        elements.append(Paragraph(
            "4. ARMS AND INTERVENTIONS", self.styles['SectionHeader']
        ))

        # Arms table
        elements.append(Paragraph("4.1 Study Arms", self.styles['SubSection']))
        arms_data = [["Arm Label", "Type", "Description", "Target N"]]
        for arm in trial.arms:
            arms_data.append([
                arm.arm_label, arm.arm_type.value,
                Paragraph(arm.description, self.styles['TableCell']),
                str(arm.target_enrollment)
            ])

        arms_table = Table(
            arms_data,
            colWidths=[1.5 * inch, 1.2 * inch, 3 * inch, 0.8 * inch]
        )
        arms_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c5282')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('PADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(arms_table)

        # Interventions
        elements.append(Paragraph("4.2 Interventions", self.styles['SubSection']))
        for interv in trial.interventions:
            interv_data = [
                ["Field", "Detail"],
                ["Name", interv.name],
                ["Type", interv.intervention_type.value],
                ["Generic Name", interv.generic_name or "N/A"],
                ["RxNorm Code", interv.rxnorm_code or "N/A"],
                ["Dosage", f"{interv.dose_value} {interv.dose_unit}" if interv.dose_value else "N/A"],
                ["Route", interv.route or "N/A"],
                ["Frequency", interv.frequency or "N/A"],
                ["Duration", interv.duration or "N/A"],
                ["Description", Paragraph(interv.description, self.styles['TableCell'])],
            ]
            interv_table = Table(interv_data, colWidths=[1.5 * inch, 5 * inch])
            interv_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a5568')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('PADDING', (0, 0), (-1, -1), 4),
            ]))
            elements.append(interv_table)
            elements.append(Spacer(1, 8))
        return elements

    def _build_eligibility_section(self, trial: ClinicalTrial) -> list:
        elements = []
        elements.append(Paragraph(
            "5. ELIGIBILITY CRITERIA", self.styles['SectionHeader']
        ))

        inclusions = [c for c in trial.eligibility_criteria
                     if c.criteria_type == "Inclusion"]
        exclusions = [c for c in trial.eligibility_criteria
                     if c.criteria_type == "Exclusion"]

        if inclusions:
            elements.append(Paragraph(
                "5.1 Inclusion Criteria", self.styles['SubSection']
            ))
            for i, crit in enumerate(inclusions, 1):
                elements.append(Paragraph(
                    f"  {i}. {crit.description}",
                    self.styles['BodyText_Custom']
                ))

        if exclusions:
            elements.append(Paragraph(
                "5.2 Exclusion Criteria", self.styles['SubSection']
            ))
            for i, crit in enumerate(exclusions, 1):
                elements.append(Paragraph(
                    f"  {i}. {crit.description}",
                    self.styles['BodyText_Custom']
                ))

        # Age/Gender summary
        first_crit = trial.eligibility_criteria[0] if trial.eligibility_criteria else None
        if first_crit:
            elements.append(Paragraph("5.3 Demographics", self.styles['SubSection']))
            elements.append(Paragraph(
                f"Ages: {first_crit.min_age or 'N/A'} to {first_crit.max_age or 'N/A'} years | "
                f"Gender: {first_crit.gender} | "
                f"Healthy Volunteers: {'Yes' if first_crit.healthy_volunteers else 'No'}",
                self.styles['BodyText_Custom']
            ))
        return elements

    def _build_outcomes_section(self, trial: ClinicalTrial) -> list:
        elements = []
        elements.append(Paragraph(
            "6. OUTCOME MEASURES", self.styles['SectionHeader']
        ))

        outcomes_data = [["Type", "Measure", "Time Frame", "Description"]]
        for outcome in trial.outcome_measures:
            outcomes_data.append([
                outcome.outcome_type,
                Paragraph(outcome.measure, self.styles['TableCell']),
                Paragraph(outcome.time_frame, self.styles['TableCell']),
                Paragraph(outcome.description, self.styles['TableCell']),
            ])

        outcomes_table = Table(
            outcomes_data,
            colWidths=[0.8 * inch, 1.8 * inch, 1.5 * inch, 2.4 * inch]
        )
        outcomes_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c5282')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('PADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(outcomes_table)
        return elements

    def _build_locations_section(self, trial: ClinicalTrial) -> list:
        elements = []
        elements.append(Paragraph(
            "7. STUDY LOCATIONS", self.styles['SectionHeader']
        ))

        elements.append(Paragraph(
            f"Regions: {', '.join(trial.regions)}",
            self.styles['BodyText_Custom']
        ))
        elements.append(Paragraph(
            f"Countries: {', '.join(trial.countries)}",
            self.styles['BodyText_Custom']
        ))

        if trial.site_locations:
            loc_data = [["Facility", "City", "Country"]]
            for loc in trial.site_locations:
                loc_data.append([loc.facility, loc.city, loc.country])

            loc_table = Table(
                loc_data,
                colWidths=[3 * inch, 1.5 * inch, 2 * inch]
            )
            loc_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c5282')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('PADDING', (0, 0), (-1, -1), 4),
            ]))
            elements.append(loc_table)
        return elements

    def _build_patient_summary_section(
        self, patients: list[Patient], trial: ClinicalTrial
    ) -> list:
        """Build aggregate patient demographics summary."""
        elements = []
        elements.append(Paragraph(
            "8. ENROLLED PATIENT DATA", self.styles['SectionHeader']
        ))
        elements.append(Paragraph(
            f"Total enrolled patients: {len(patients)}",
            self.styles['BodyText_Custom']
        ))

        # Demographics summary table
        ages = [p.age for p in patients]
        male_count = sum(1 for p in patients if p.sex == Sex.MALE)
        female_count = sum(1 for p in patients if p.sex == Sex.FEMALE)
        total_aes = sum(len(p.adverse_events) for p in patients)
        serious_aes = sum(
            sum(1 for ae in p.adverse_events if ae.serious) for p in patients
        )

        summary_data = [
            ["Metric", "Value"],
            ["Total Patients", str(len(patients))],
            ["Age Range", f"{min(ages)} - {max(ages)} years"],
            ["Mean Age", f"{sum(ages) / len(ages):.1f} years"],
            ["Male / Female", f"{male_count} / {female_count}"],
            ["Total Adverse Events", str(total_aes)],
            ["Serious Adverse Events", str(serious_aes)],
        ]

        # Arm distribution
        arm_counts = {}
        for p in patients:
            arm_counts[p.arm_assigned] = arm_counts.get(p.arm_assigned, 0) + 1
        for arm, count in arm_counts.items():
            summary_data.append([f"Arm: {arm}", str(count)])

        summary_table = Table(summary_data, colWidths=[3 * inch, 3.5 * inch])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c5282')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('PADDING', (0, 0), (-1, -1), 5),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f7fafc')]),
        ]))
        elements.append(summary_table)
        return elements

    def _build_patient_detail_section(
        self, patient: Patient, trial: ClinicalTrial
    ) -> list:
        """Build detailed individual patient case report."""
        elements = []
        elements.append(Paragraph(
            f"PATIENT CASE REPORT: {patient.subject_id}",
            self.styles['SectionHeader']
        ))

        # Demographics
        demo_data = [
            ["Field", "Value"],
            ["Subject ID", patient.subject_id],
            ["Site", patient.site_id or "N/A"],
            ["Age", f"{patient.age} years"],
            ["Sex", patient.sex.value],
            ["Race", patient.race or "N/A"],
            ["Ethnicity", patient.ethnicity or "N/A"],
            ["Country", patient.country or "N/A"],
            ["Enrollment Date", str(patient.enrollment_date)],
            ["Arm Assigned", patient.arm_assigned or "N/A"],
            ["Disposition", patient.disposition_status],
        ]
        demo_table = Table(demo_data, colWidths=[2 * inch, 4.5 * inch])
        demo_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a5568')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('PADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(demo_table)
        elements.append(Spacer(1, 10))

        # Conditions
        if patient.conditions:
            elements.append(Paragraph("Medical History", self.styles['SubSection']))
            cond_data = [["Condition", "ICD-10", "Severity", "Ongoing", "Onset"]]
            for c in patient.conditions:
                cond_data.append([
                    c.condition_name, c.icd10_code, c.severity.value,
                    "Yes" if c.is_ongoing else "No",
                    str(c.onset_date) if c.onset_date else "N/A"
                ])
            cond_table = Table(
                cond_data,
                colWidths=[2 * inch, 0.8 * inch, 0.8 * inch, 0.7 * inch, 1 * inch]
            )
            cond_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('PADDING', (0, 0), (-1, -1), 3),
            ]))
            elements.append(cond_table)
            elements.append(Spacer(1, 8))

        # Medications
        if patient.medications:
            elements.append(Paragraph("Medications", self.styles['SubSection']))
            med_data = [["Medication", "Dose", "Route", "Frequency", "Indication"]]
            for m in patient.medications:
                med_data.append([
                    m.medication_name,
                    f"{m.dose_value} {m.dose_unit}" if m.dose_value else "N/A",
                    m.route or "N/A",
                    m.frequency or "N/A",
                    Paragraph(m.indication or "N/A", self.styles['TableCell'])
                ])
            med_table = Table(
                med_data,
                colWidths=[1.5 * inch, 0.8 * inch, 0.8 * inch, 1 * inch, 1.5 * inch]
            )
            med_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('PADDING', (0, 0), (-1, -1), 3),
            ]))
            elements.append(med_table)
            elements.append(Spacer(1, 8))

        # Adverse Events
        if patient.adverse_events:
            elements.append(Paragraph("Adverse Events", self.styles['SubSection']))
            ae_data = [["AE Term", "MedDRA PT", "Severity", "Serious",
                       "Causality", "Outcome"]]
            for ae in patient.adverse_events:
                ae_data.append([
                    ae.ae_term,
                    ae.meddra_pt or "N/A",
                    ae.severity.value,
                    "Yes" if ae.serious else "No",
                    ae.causality.value,
                    ae.outcome
                ])
            ae_table = Table(
                ae_data,
                colWidths=[1.2 * inch, 1 * inch, 0.7 * inch, 0.6 * inch,
                          1 * inch, 0.8 * inch]
            )
            ae_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e53e3e')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('PADDING', (0, 0), (-1, -1), 3),
            ]))
            elements.append(ae_table)
            elements.append(Spacer(1, 8))

        # Lab Results (latest visit only to save space)
        if patient.lab_results:
            latest_visit = patient.lab_results[-1].visit_name
            latest_labs = [
                lr for lr in patient.lab_results
                if lr.visit_name == latest_visit
            ]
            elements.append(Paragraph(
                f"Laboratory Results (Visit: {latest_visit})",
                self.styles['SubSection']
            ))
            lab_data = [["Test", "LOINC", "Value", "Unit", "Range", "Flag"]]
            for lab in latest_labs:
                flag_color = (
                    colors.red if lab.abnormal_flag in ('H', 'L')
                    else colors.black
                )
                lab_data.append([
                    lab.test_name, lab.loinc_code or "N/A",
                    str(lab.result_value), lab.result_unit,
                    f"{lab.reference_low}-{lab.reference_high}",
                    lab.abnormal_flag or "N"
                ])
            lab_table = Table(
                lab_data,
                colWidths=[1.5 * inch, 0.7 * inch, 0.7 * inch, 0.7 * inch,
                          1 * inch, 0.5 * inch]
            )
            lab_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#38a169')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('PADDING', (0, 0), (-1, -1), 3),
            ]))
            elements.append(lab_table)

        return elements

    def _build_remaining_patients_table(
        self, patients: list[Patient]
    ) -> list:
        """Compact summary table for remaining patients."""
        elements = []
        elements.append(Paragraph(
            "ADDITIONAL ENROLLED PATIENTS (Summary)",
            self.styles['SectionHeader']
        ))

        data = [["Subject ID", "Age", "Sex", "Arm", "Status",
                "Conditions", "AEs", "Serious AEs"]]
        for p in patients:
            data.append([
                p.subject_id, str(p.age), p.sex.value,
                p.arm_assigned or "N/A",
                p.disposition_status,
                str(len(p.conditions)),
                str(len(p.adverse_events)),
                str(sum(1 for ae in p.adverse_events if ae.serious))
            ])

        table = Table(
            data,
            colWidths=[1.2 * inch, 0.5 * inch, 0.4 * inch, 1.2 * inch,
                      0.8 * inch, 0.7 * inch, 0.5 * inch, 0.7 * inch]
        )
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c5282')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('PADDING', (0, 0), (-1, -1), 3),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1),
             [colors.white, colors.HexColor('#f7fafc')]),
        ]))
        elements.append(table)
        return elements