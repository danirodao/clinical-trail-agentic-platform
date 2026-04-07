# processor/main.py
"""
Processor container entry point.
Runs continuously: consumes Kafka events, processes PDFs.

The processor:
1. Starts up and initializes all store connections
2. Starts a health check HTTP server (for Docker/K8s)
3. Enters the Kafka consumer loop
4. For each pdf-generated event:
   a. Downloads PDF from MinIO
   b. Parses and extracts entities
   c. Generates embeddings
   d. Loads into PostgreSQL, Qdrant, and Neo4j
5. Commits Kafka offset after successful processing
6. Handles retries and dead letter queue on failure
"""
import asyncio
import logging
import sys
from aiohttp import web

from shared.config import AppConfig
from processor.consumer import PDFEventConsumer
from processor.orchestrator import ProcessingOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("processor")

# ═══════════════════════════════════════════
# Health Check Server
# Docker/K8s can probe this to verify the processor is alive
# ═══════════════════════════════════════════

health_status = {"ready": False, "messages_processed": 0, "errors": 0}


async def health_handler(request):
    if health_status["ready"]:
        return web.json_response(health_status, status=200)
    return web.json_response(health_status, status=503)


async def start_health_server():
    app = web.Application()
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8081)
    await site.start()
    logger.info("Health check server started on :8081")
    return runner


# ═══════════════════════════════════════════
# Main
# ═══════════════════════════════════════════

async def main():
    logger.info("=" * 60)
    logger.info("CLINICAL TRIAL PDF PROCESSOR")
    logger.info("=" * 60)

    # ── Load configuration ──
    config = AppConfig()
    logger.info(f"Kafka: {config.kafka.bootstrap_servers}")
    logger.info(f"Topic: {config.kafka.topic_pdf_generated}")
    logger.info(f"Consumer Group: {config.kafka.consumer_group}")

    # ── Start health check server ──
    health_runner = await start_health_server()

    # ── Initialize the processing pipeline ──
    orchestrator = ProcessingOrchestrator(config)
    await orchestrator.initialize()

    # ── Create the Kafka consumer ──
    consumer = PDFEventConsumer(
        kafka_config=config.kafka,
        process_callback=orchestrator.process_event
    )

    # ── Mark as ready ──
    health_status["ready"] = True
    logger.info("Processor is READY — entering consumer loop")

    try:
        # ── Enter the consumer loop (blocks until shutdown signal) ──
        await consumer.start()
    except Exception as e:
        logger.error(f"Fatal error in consumer: {e}", exc_info=True)
    finally:
        logger.info("Shutting down processor...")
        health_status["ready"] = False
        await orchestrator.shutdown()
        await health_runner.cleanup()
        logger.info("Processor shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())