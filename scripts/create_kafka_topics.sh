#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# create_kafka_topics.sh
#
# Creates all required Kafka topics for the clinical trial
# pipeline. Idempotent — safe to run multiple times.
#
# Usage:
#   ./scripts/create_kafka_topics.sh                  # Default (local Docker)
#   ./scripts/create_kafka_topics.sh kafka:29092      # Custom bootstrap server
#   PARTITIONS=6 ./scripts/create_kafka_topics.sh     # Override partitions
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# ── Configuration ──
BOOTSTRAP_SERVER="${1:-localhost:9092}"
PARTITIONS="${PARTITIONS:-3}"
REPLICATION_FACTOR="${REPLICATION_FACTOR:-1}"
KAFKA_BIN="${KAFKA_BIN:-kafka-topics}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()  { echo -e "${BLUE}[INFO]${NC}  $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── Wait for Kafka to be ready ──
wait_for_kafka() {
    local max_attempts=30
    local attempt=1

    log_info "Waiting for Kafka at ${BOOTSTRAP_SERVER}..."

    while [ $attempt -le $max_attempts ]; do
        if $KAFKA_BIN --bootstrap-server "$BOOTSTRAP_SERVER" --list &>/dev/null; then
            log_ok "Kafka is ready"
            return 0
        fi

        log_info "Attempt ${attempt}/${max_attempts} — Kafka not ready, retrying in 5s..."
        sleep 5
        attempt=$((attempt + 1))
    done

    log_error "Kafka did not become ready after ${max_attempts} attempts"
    exit 1
}

# ── Create a single topic ──
create_topic() {
    local topic_name="$1"
    local partitions="${2:-$PARTITIONS}"
    local replication="${3:-$REPLICATION_FACTOR}"
    shift 3
    local extra_configs=("$@")

    # Check if topic already exists
    if $KAFKA_BIN --bootstrap-server "$BOOTSTRAP_SERVER" --describe --topic "$topic_name" &>/dev/null; then
        log_warn "Topic '${topic_name}' already exists — skipping"
        return 0
    fi

    # Build the create command
    local cmd=(
        "$KAFKA_BIN"
        --bootstrap-server "$BOOTSTRAP_SERVER"
        --create
        --topic "$topic_name"
        --partitions "$partitions"
        --replication-factor "$replication"
    )

    # Add extra configs (e.g., retention, max message bytes)
    for config in "${extra_configs[@]}"; do
        cmd+=(--config "$config")
    done

    if "${cmd[@]}"; then
        log_ok "Created topic: ${topic_name} (partitions=${partitions}, replication=${replication})"
    else
        log_error "Failed to create topic: ${topic_name}"
        return 1
    fi
}

# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════

echo ""
echo "═══════════════════════════════════════════"
echo "  Clinical Trial Platform — Kafka Topics"
echo "═══════════════════════════════════════════"
echo "  Bootstrap Server:   ${BOOTSTRAP_SERVER}"
echo "  Default Partitions: ${PARTITIONS}"
echo "  Replication Factor: ${REPLICATION_FACTOR}"
echo "═══════════════════════════════════════════"
echo ""

wait_for_kafka

# ── 1. PDF Generated Events ──
# Published by the Generator when a new PDF is uploaded to MinIO.
# Consumed by the Processor to trigger the ingestion pipeline.
# Partitioned by NCT ID — all events for same trial go to same partition.
#
# Retention: 7 days (enough for recovery/replay)
# Max message size: 1MB (events are lightweight; PDFs are in MinIO)
create_topic \
    "pdf-generated" \
    "$PARTITIONS" \
    "$REPLICATION_FACTOR" \
    "retention.ms=604800000" \
    "max.message.bytes=1048576" \
    "cleanup.policy=delete" \
    "min.insync.replicas=1"

# ── 2. Processing Status Events ──
# Published by the Processor to report progress:
#   - pdf.processing.started
#   - pdf.processing.completed
#   - pdf.processing.failed
# Used for monitoring, dashboards, and alerting.
#
# Retention: 30 days (for auditing and analytics)
create_topic \
    "pdf-processing-status" \
    "$PARTITIONS" \
    "$REPLICATION_FACTOR" \
    "retention.ms=2592000000" \
    "max.message.bytes=524288" \
    "cleanup.policy=delete"

# ── 3. Dead Letter Queue (DLQ) ──
# Messages that permanently fail processing after all retries.
# Infinite retention — DLQ messages must be manually reviewed.
# Single partition — low volume, ordering not critical.
create_topic \
    "pdf-processing-dlq" \
    1 \
    "$REPLICATION_FACTOR" \
    "retention.ms=-1" \
    "max.message.bytes=1048576" \
    "cleanup.policy=compact"

# ── 4. Ingestion Commands (future use) ──
# For on-demand reprocessing requests, e.g., "reprocess NCT12345678"
# Reserved for the consumer API to trigger reprocessing.
create_topic \
    "ingestion-commands" \
    "$PARTITIONS" \
    "$REPLICATION_FACTOR" \
    "retention.ms=86400000" \
    "max.message.bytes=262144" \
    "cleanup.policy=delete"

# ── Verify all topics ──
echo ""
log_info "Listing all topics:"
echo "───────────────────────────────────────────"
$KAFKA_BIN --bootstrap-server "$BOOTSTRAP_SERVER" --list | while read -r topic; do
    echo "  • ${topic}"
done
echo "───────────────────────────────────────────"

# ── Show topic details ──
echo ""
log_info "Topic details:"
echo ""
for topic in "pdf-generated" "pdf-processing-status" "pdf-processing-dlq" "ingestion-commands"; do
    echo "── ${topic} ──"
    $KAFKA_BIN --bootstrap-server "$BOOTSTRAP_SERVER" --describe --topic "$topic" 2>/dev/null | head -5
    echo ""
done

log_ok "All Kafka topics created successfully"
echo ""