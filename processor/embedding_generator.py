from __future__ import annotations
from openai import AsyncOpenAI
from dataclasses import dataclass
import hashlib
import json
import uuid
import logging

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingChunk:
    """A chunk of text with its embedding and metadata."""
    chunk_id: str
    text: str
    embedding: list[float]
    chunk_type: str
    metadata: dict
    token_count: int


class ClinicalTrialEmbeddingGenerator:

    # ✅ Minimum text length to attempt embedding
    MIN_TEXT_LENGTH = 10

    def __init__(
        self,
        openai_client: AsyncOpenAI,
        embedding_model: str = "text-embedding-3-large",
        max_chunk_tokens: int = 512,
        expected_dim: int = 3072
    ):
        self.llm = openai_client
        self.model = embedding_model
        self.max_tokens = max_chunk_tokens
        self.expected_dim = expected_dim

    async def generate_trial_chunks(
        self,
        trial_data: dict,
        trial_id: str,
        nct_id: str
    ) -> list[EmbeddingChunk]:
        chunks = []

        summary_text = self._build_trial_summary_text(trial_data)
        chunk = await self._safe_create_chunk(
            text=summary_text,
            chunk_type="trial_summary",
            metadata={
                "trial_id": trial_id,
                "nct_id": nct_id,
                "phase": trial_data.get("phase", ""),
                "therapeutic_area": trial_data.get("therapeutic_area", ""),
                "status": trial_data.get("overall_status", ""),
                "section": "summary"
            }
        )
        if chunk:
            chunks.append(chunk)

        design_text = self._build_design_text(trial_data)
        chunk = await self._safe_create_chunk(
            text=design_text,
            chunk_type="trial_design",
            metadata={
                "trial_id": trial_id,
                "nct_id": nct_id,
                "section": "design"
            }
        )
        if chunk:
            chunks.append(chunk)

        for i, crit in enumerate(
            trial_data.get("eligibility_criteria", [])
        ):
            crit_text = (
                f"Eligibility ({crit.get('criteria_type', 'Unknown')}): "
                f"{crit.get('description', '')}"
            )
            chunk = await self._safe_create_chunk(
                text=crit_text,
                chunk_type="eligibility_criterion",
                metadata={
                    "trial_id": trial_id,
                    "nct_id": nct_id,
                    "criteria_type": crit.get("criteria_type", ""),
                    "section": "eligibility",
                    "criterion_index": i
                }
            )
            if chunk:
                chunks.append(chunk)

        for interv in trial_data.get("interventions", []):
            interv_text = self._build_intervention_text(interv)
            chunk = await self._safe_create_chunk(
                text=interv_text,
                chunk_type="intervention",
                metadata={
                    "trial_id": trial_id,
                    "nct_id": nct_id,
                    "drug_name": interv.get("name", ""),
                    "rxnorm_code": interv.get("rxnorm_code", ""),
                    "section": "intervention"
                }
            )
            if chunk:
                chunks.append(chunk)

        outcomes_text = self._build_outcomes_text(trial_data)
        chunk = await self._safe_create_chunk(
            text=outcomes_text,
            chunk_type="outcome_measures",
            metadata={
                "trial_id": trial_id,
                "nct_id": nct_id,
                "section": "outcomes"
            }
        )
        if chunk:
            chunks.append(chunk)

        return chunks

    async def generate_patient_chunks(
        self,
        patient_data: dict,
        patient_id: str,
        trial_id: str,
        nct_id: str
    ) -> list[EmbeddingChunk]:
        chunks = []

        narrative = self._build_patient_narrative(patient_data)
        chunk = await self._safe_create_chunk(
            text=narrative,
            chunk_type="patient_narrative",
            metadata={
                "patient_id": patient_id,
                "trial_id": trial_id,
                "nct_id": nct_id,
                "age": patient_data.get("age"),
                "sex": patient_data.get("sex"),
                "arm": patient_data.get("arm_assigned", ""),
                "section": "patient_narrative"
            }
        )
        if chunk:
            chunks.append(chunk)

        for ae in patient_data.get("adverse_events", []):
            if ae.get("serious"):
                ae_text = (
                    f"Serious Adverse Event for patient "
                    f"{patient_data.get('subject_id', 'Unknown')}: "
                    f"{ae.get('ae_term', '')} "
                    f"(MedDRA: {ae.get('meddra_pt', 'N/A')}). "
                    f"Severity: {ae.get('severity', 'Unknown')}. "
                    f"Causality: {ae.get('causality', 'Unknown')}. "
                    f"Outcome: {ae.get('outcome', 'Unknown')}. "
                    f"Action: {ae.get('action_taken', 'None')}."
                )
                chunk = await self._safe_create_chunk(
                    text=ae_text,
                    chunk_type="serious_adverse_event",
                    metadata={
                        "patient_id": patient_id,
                        "trial_id": trial_id,
                        "nct_id": nct_id,
                        "ae_term": ae.get("ae_term", ""),
                        "meddra_pt": ae.get("meddra_pt", ""),
                        "serious": True,
                        "section": "adverse_event"
                    }
                )
                if chunk:
                    chunks.append(chunk)

        return chunks

    # ═══════════════════════════════════════════════════════════════
    # ✅ NEW: Safe chunk creation with validation
    # ═══════════════════════════════════════════════════════════════

    async def _safe_create_chunk(
        self,
        text: str,
        chunk_type: str,
        metadata: dict
    ) -> EmbeddingChunk | None:
        """
        Create a chunk with validation. Returns None if:
        - text is empty/too short
        - embedding API returns empty vector
        """
        # ── Validate text ──
        cleaned = (text or "").strip()
        if len(cleaned) < self.MIN_TEXT_LENGTH:
            logger.debug(
                f"Skipping chunk '{chunk_type}': text too short "
                f"({len(cleaned)} chars)"
            )
            return None

        try:
            chunk = await self._create_chunk(cleaned, chunk_type, metadata)

            # ── Validate embedding ──
            if not chunk or not chunk.embedding:
                logger.error(
                    f"CRITICAL: Embedding API returned None for chunk '{chunk_type}'"
                )
                return None

            actual_dim = len(chunk.embedding)
            if actual_dim != self.expected_dim:
                logger.error(
                    f"CRITICAL: Embedding dimension mismatch for chunk '{chunk_type}'. "
                    f"Expected {self.expected_dim}, got {actual_dim}. "
                    f"Text length: {len(cleaned)} chars."
                )
                return None

            return chunk

        except Exception as e:
            logger.warning(
                f"Failed to create chunk '{chunk_type}': {e}"
            )
            return None

    async def _create_chunk(
        self,
        text: str,
        chunk_type: str,
        metadata: dict
    ) -> EmbeddingChunk:
        response = await self.llm.embeddings.create(
            model=self.model,
            input=text
        )
        embedding = response.data[0].embedding

        hash_input = (
            f"{chunk_type}:{json.dumps(metadata, sort_keys=True)}:{text[:200]}"
        )
        chunk_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, hash_input))

        return EmbeddingChunk(
            chunk_id=chunk_id,
            text=text,
            embedding=embedding,
            chunk_type=chunk_type,
            metadata=metadata,
            token_count=response.usage.total_tokens
        )

    def _build_trial_summary_text(self, trial_data: dict) -> str:
        return (
            f"Clinical Trial {trial_data.get('nct_id', '')}: "
            f"{trial_data.get('title', trial_data.get('official_title', ''))}. "
            f"Phase: {trial_data.get('phase', 'N/A')}. "
            f"Status: {trial_data.get('overall_status', trial_data.get('status', 'N/A'))}. "
            f"Therapeutic area: {trial_data.get('therapeutic_area', 'N/A')}. "
            f"Sponsor: {trial_data.get('lead_sponsor', 'N/A')}. "
            f"Enrollment: {trial_data.get('enrollment_count', 'N/A')}. "
            f"{trial_data.get('brief_summary', '')}"
        )

    def _build_design_text(self, trial_data: dict) -> str:
        return (
            f"Study design for {trial_data.get('nct_id', '')}: "
            f"Type: {trial_data.get('study_type', 'N/A')}. "
            f"Allocation: {trial_data.get('allocation', 'N/A')}. "
            f"Model: {trial_data.get('intervention_model', 'N/A')}. "
            f"Masking: {trial_data.get('masking', 'N/A')}. "
            f"Purpose: {trial_data.get('primary_purpose', 'N/A')}."
        )

    def _build_intervention_text(self, interv: dict) -> str:
        return (
            f"Intervention: {interv.get('name', '')} "
            f"({interv.get('intervention_type', interv.get('type', 'Drug'))}). "
            f"Generic: {interv.get('generic_name', 'N/A')}. "
            f"RxNorm: {interv.get('rxnorm_code', 'N/A')}. "
            f"Dose: {interv.get('dosage', interv.get('dose_value', 'N/A'))} "
            f"{interv.get('dose_unit', '')}. "
            f"Route: {interv.get('route', 'N/A')}. "
            f"Frequency: {interv.get('frequency', 'N/A')}. "
            f"Duration: {interv.get('duration', 'N/A')}. "
            f"{interv.get('description', '')}"
        )

    def _build_outcomes_text(self, trial_data: dict) -> str:
        outcomes = trial_data.get("outcome_measures", [])
        if not outcomes:
            return ""
        parts = [
            f"Outcome measures for {trial_data.get('nct_id', '')}:"
        ]
        for o in outcomes:
            parts.append(
                f" {o.get('outcome_type', 'Other')}: "
                f"{o.get('measure', '')} "
                f"(timeframe: {o.get('time_frame', 'N/A')})"
            )
        return " ".join(parts)

    def _build_patient_narrative(self, patient_data: dict) -> str:
        parts = [
            f"Patient {patient_data.get('subject_id', 'Unknown')}:",
            f"{patient_data.get('age', 'N/A')} year old "
            f"{patient_data.get('sex', 'Unknown')}.",
            f"Arm: {patient_data.get('arm_assigned', 'N/A')}.",
            f"Status: {patient_data.get('disposition_status', 'N/A')}.",
        ]

        conditions = patient_data.get('conditions', [])
        if conditions:
            cond_names = [c.get('condition_name', '') for c in conditions]
            parts.append(f"Conditions: {', '.join(cond_names)}.")

        meds = patient_data.get('medications', [])
        if meds:
            med_names = [m.get('medication_name', '') for m in meds]
            parts.append(f"Medications: {', '.join(med_names)}.")

        aes = patient_data.get('adverse_events', [])
        if aes:
            ae_summary = [
                f"{ae.get('ae_term', '')} "
                f"({'serious' if ae.get('serious') else ae.get('severity', '')})"
                for ae in aes
            ]
            parts.append(f"Adverse events: {', '.join(ae_summary)}.")

        return " ".join(parts)