# generator/main.py
"""
Generator container entry point.
Runs on-demand: generates synthetic PDFs, uploads to MinIO,
publishes events to Kafka, then exits.

Usage:
  docker compose run --rm generator
  docker compose run --rm -e NUM_TRIALS=5 -e PATIENTS_PER_TRIAL=10 generator
"""
import os
import sys
import time
import logging
import tempfile
import shutil

from shared.config import KafkaConfig, MinIOConfig
from generator.synthetic_data import ClinicalTrialGenerator
from generator.pdf_builder import ClinicalTrialPDFBuilder
from generator.publisher import PDFPublisher

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("generator")


def main():
    start_time = time.time()

    # ── Configuration from environment ──
    num_trials = int(os.environ.get("NUM_TRIALS", 10))
    patients_per_trial = int(os.environ.get("PATIENTS_PER_TRIAL", 20))
    seed = int(os.environ.get("SEED", 42))

    logger.info("=" * 60)
    logger.info("CLINICAL TRIAL PDF GENERATOR")
    logger.info(f"  Trials to generate: {num_trials}")
    logger.info(f"  Patients per trial: {patients_per_trial}")
    logger.info(f"  Seed: {seed}")
    logger.info("=" * 60)

    # ── Initialize components ──
    kafka_config = KafkaConfig()
    minio_config = MinIOConfig()
    publisher = PDFPublisher(kafka_config, minio_config)
    generator = ClinicalTrialGenerator(seed=seed)

    # Use a temp directory for PDFs (container-ephemeral)
    output_dir = tempfile.mkdtemp(prefix="ct_gen_")
    pdf_builder = ClinicalTrialPDFBuilder(output_dir=output_dir)

    logger.info(f"Temporary PDF directory: {output_dir}")

    try:
        # ── Generate trials ──
        logger.info("Generating synthetic trial data...")
        documents = generator.generate_batch(
            num_trials=num_trials,
            patients_per_trial=patients_per_trial
        )
        logger.info(f"Generated {len(documents)} trial documents in memory")

        # ── Build PDFs and publish ──
        results = []
        for i, doc in enumerate(documents, 1):
            trial = doc.trial
            logger.info(
                f"[{i}/{len(documents)}] Processing {trial.nct_id} "
                f"({trial.therapeutic_area}, {trial.phase.value}, "
                f"{len(doc.patients)} patients)"
            )

            # Build PDF
            pdf_path = pdf_builder.build_trial_protocol_pdf(doc)
            logger.info(f"  PDF created: {pdf_path}")

            # Upload to MinIO + publish Kafka event
            event = publisher.publish_trial_pdf(
                local_pdf_path=pdf_path,
                nct_id=trial.nct_id,
                therapeutic_area=trial.therapeutic_area,
                phase=trial.phase.value,
                sponsor=trial.lead_sponsor,
                num_patients=len(doc.patients),
                regions=trial.regions,
                generation_seed=seed
            )
            logger.info(
                f"  Published: event_id={event.event_id}, "
                f"object_key={event.object_key}"
            )

            results.append({
                "nct_id": trial.nct_id,
                "event_id": event.event_id,
                "object_key": event.object_key,
                "patients": len(doc.patients)
            })

            # Remove local PDF (already in MinIO)
            os.remove(pdf_path)

        # ── Summary ──
        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info("GENERATION COMPLETE")
        logger.info(f"  Trials generated:  {len(results)}")
        logger.info(f"  Total patients:    {sum(r['patients'] for r in results)}")
        logger.info(f"  Elapsed time:      {elapsed:.1f}s")
        logger.info("=" * 60)

        for r in results:
            logger.info(
                f"  ✅ {r['nct_id']}: {r['patients']} patients → {r['object_key']}"
            )

    finally:
        publisher.close()
        shutil.rmtree(output_dir, ignore_errors=True)
        logger.info("Cleanup complete. Generator exiting.")


if __name__ == "__main__":
    main()