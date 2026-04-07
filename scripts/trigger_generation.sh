#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# trigger_generation.sh
#
# Triggers the generator container to produce synthetic
# clinical trial PDFs, upload them to MinIO, and publish
# Kafka events for the processor.
#
# The generator container runs, does its work, and exits.
# The processor (already running) picks up events automatically.
#
# Usage:
#   ./scripts/trigger_generation.sh                   # Default: 10 trials, 20 patients
#   ./scripts/trigger_generation.sh --trials 5        # 5 trials
#   ./scripts/trigger_generation.sh --trials 20 --patients 50 --seed 999
#   ./scripts/trigger_generation.sh --help
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# ── Defaults ──
NUM_TRIALS=10
PATIENTS_PER_TRIAL=20
SEED=42
WAIT_FOR_PROCESSING=false
COMPOSE_FILE="docker-compose.yml"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC}  $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── Parse arguments ──
usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Triggers the clinical trial PDF generator container.

Options:
  --trials, -t NUM          Number of trials to generate (default: ${NUM_TRIALS})
  --patients, -p NUM        Patients per trial (default: ${PATIENTS_PER_TRIAL})
  --seed, -s NUM            Random seed for reproducibility (default: ${SEED})
  --wait, -w                Wait for processor to finish all events
  --compose-file, -f FILE   Path to docker-compose.yml (default: ${COMPOSE_FILE})
  --help, -h                Show this help message

Examples:
  $(basename "$0")                                   # 10 trials, 20 patients each
  $(basename "$0") --trials 5 --patients 10          # 5 small trials
  $(basename "$0") --trials 50 --patients 30 --wait  # Large batch, wait for completion
  $(basename "$0") --seed 123                        # Reproducible generation

EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --trials|-t)        NUM_TRIALS="$2";          shift 2 ;;
        --patients|-p)      PATIENTS_PER_TRIAL="$2";  shift 2 ;;
        --seed|-s)          SEED="$2";                shift 2 ;;
        --wait|-w)          WAIT_FOR_PROCESSING=true; shift   ;;
        --compose-file|-f)  COMPOSE_FILE="$2";        shift 2 ;;
        --help|-h)          usage ;;
        *)
            log_error "Unknown option: $1"
            echo "Run '$(basename "$0") --help' for usage"
            exit 1
            ;;
    esac
done

# ── Validate inputs ──
if ! [[ "$NUM_TRIALS" =~ ^[0-9]+$ ]] || [ "$NUM_TRIALS" -lt 1 ]; then
    log_error "--trials must be a positive integer (got: ${NUM_TRIALS})"
    exit 1
fi

if ! [[ "$PATIENTS_PER_TRIAL" =~ ^[0-9]+$ ]] || [ "$PATIENTS_PER_TRIAL" -lt 1 ]; then
    log_error "--patients must be a positive integer (got: ${PATIENTS_PER_TRIAL})"
    exit 1
fi

if ! [[ "$SEED" =~ ^[0-9]+$ ]]; then
    log_error "--seed must be a non-negative integer (got: ${SEED})"
    exit 1
fi

# ── Pre-flight checks ──
preflight_checks() {
    log_info "Running pre-flight checks..."

    # Check docker compose is available
    if ! command -v docker &>/dev/null; then
        log_error "Docker is not installed or not in PATH"
        exit 1
    fi

    # Check compose file exists
    if [ ! -f "$COMPOSE_FILE" ]; then
        log_error "Compose file not found: ${COMPOSE_FILE}"
        exit 1
    fi

    # Check infrastructure is running
    local required_services=("kafka" "minio" "postgres" "qdrant" "neo4j")
    local all_running=true

    for service in "${required_services[@]}"; do
        local status
        status=$(docker compose -f "$COMPOSE_FILE" ps --format json "$service" 2>/dev/null | \
                 python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('Health','') or data.get('State',''))" 2>/dev/null || echo "not found")

        if [[ "$status" == *"healthy"* ]] || [[ "$status" == *"running"* ]]; then
            log_ok "  ${service}: running"
        else
            log_error "  ${service}: NOT running (status: ${status})"
            all_running=false
        fi
    done

    if [ "$all_running" = false ]; then
        echo ""
        log_error "Infrastructure is not fully running."
        log_info "Start it with: docker compose up -d"
        exit 1
    fi

    # Check processor is running
    local processor_status
    processor_status=$(docker compose -f "$COMPOSE_FILE" ps --format json "processor" 2>/dev/null | \
                       python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('State',''))" 2>/dev/null || echo "not found")

    if [[ "$processor_status" == *"running"* ]]; then
        log_ok "  processor: running (will consume generated events)"
    else
        log_warn "  processor: NOT running — events will queue in Kafka"
        log_info "  Start it with: docker compose up -d processor"
    fi

    log_ok "Pre-flight checks passed"
}

# ── Get Kafka topic lag (messages waiting to be processed) ──
get_kafka_lag() {
    # Returns the total number of unconsumed messages in pdf-generated topic
    docker compose -f "$COMPOSE_FILE" exec -T kafka \
        kafka-consumer-groups \
        --bootstrap-server localhost:29092 \
        --group pdf-processor-group \
        --describe 2>/dev/null | \
    grep "pdf-generated" | \
    awk '{ sum += $6 } END { print sum+0 }' 2>/dev/null || echo "unknown"
}

# ── Wait for processor to drain all events ──
wait_for_processing() {
    log_info "Waiting for processor to finish all events..."

    local max_wait=600   # 10 minute max wait
    local elapsed=0
    local poll_interval=5
    local consecutive_zero=0

    while [ $elapsed -lt $max_wait ]; do
        local lag
        lag=$(get_kafka_lag)

        if [[ "$lag" == "unknown" ]]; then
            log_warn "Cannot determine Kafka lag — retrying..."
            sleep $poll_interval
            elapsed=$((elapsed + poll_interval))
            continue
        fi

        if [ "$lag" -eq 0 ]; then
            consecutive_zero=$((consecutive_zero + 1))
            # Wait for 3 consecutive zero-lag checks (to handle in-flight processing)
            if [ $consecutive_zero -ge 3 ]; then
                log_ok "All events processed (lag=0 for ${consecutive_zero} checks)"
                return 0
            fi
        else
            consecutive_zero=0
            log_info "  Messages remaining: ${lag} (elapsed: ${elapsed}s)"
        fi

        sleep $poll_interval
        elapsed=$((elapsed + poll_interval))
    done

    log_warn "Timed out waiting for processing after ${max_wait}s"
    return 1
}

# ── Show final statistics ──
show_statistics() {
    echo ""
    echo "═══════════════════════════════════════════"
    echo "  Post-Generation Statistics"
    echo "═══════════════════════════════════════════"

    # PostgreSQL counts
    local trial_count patient_count ae_count
    trial_count=$(docker compose -f "$COMPOSE_FILE" exec -T postgres \
        psql -U ctuser -d clinical_trials -t -c \
        "SELECT COUNT(*) FROM clinical_trial;" 2>/dev/null | tr -d ' ' || echo "?")

    patient_count=$(docker compose -f "$COMPOSE_FILE" exec -T postgres \
        psql -U ctuser -d clinical_trials -t -c \
        "SELECT COUNT(*) FROM patient;" 2>/dev/null | tr -d ' ' || echo "?")

    ae_count=$(docker compose -f "$COMPOSE_FILE" exec -T postgres \
        psql -U ctuser -d clinical_trials -t -c \
        "SELECT COUNT(*) FROM adverse_event;" 2>/dev/null | tr -d ' ' || echo "?")

    echo -e "  ${CYAN}PostgreSQL:${NC}"
    echo "    Clinical Trials:  ${trial_count}"
    echo "    Patients:         ${patient_count}"
    echo "    Adverse Events:   ${ae_count}"

    # Qdrant counts
    local vector_count
    vector_count=$(curl -s http://localhost:6333/collections/clinical_trial_embeddings 2>/dev/null | \
        python3 -c "import sys, json; print(json.load(sys.stdin).get('result',{}).get('points_count','?'))" 2>/dev/null || echo "?")

    echo -e "  ${CYAN}Qdrant:${NC}"
    echo "    Embedding Vectors: ${vector_count}"

    # Neo4j counts
    local node_count rel_count
    node_count=$(docker compose -f "$COMPOSE_FILE" exec -T neo4j \
        cypher-shell -u neo4j -p neo4jpassword \
        "MATCH (n) RETURN count(n) AS c;" 2>/dev/null | tail -1 | tr -d ' ' || echo "?")

    rel_count=$(docker compose -f "$COMPOSE_FILE" exec -T neo4j \
        cypher-shell -u neo4j -p neo4jpassword \
        "MATCH ()-[r]->() RETURN count(r) AS c;" 2>/dev/null | tail -1 | tr -d ' ' || echo "?")

    echo -e "  ${CYAN}Neo4j:${NC}"
    echo "    Graph Nodes:         ${node_count}"
    echo "    Graph Relationships: ${rel_count}"

    # Kafka lag
    local lag
    lag=$(get_kafka_lag)
    echo -e "  ${CYAN}Kafka:${NC}"
    echo "    Pending Messages:  ${lag}"

    # MinIO
    local pdf_count
    pdf_count=$(docker compose -f "$COMPOSE_FILE" exec -T minio \
        mc ls --recursive local/clinical-trial-pdfs 2>/dev/null | wc -l | tr -d ' ' || echo "?")

    echo -e "  ${CYAN}MinIO:${NC}"
    echo "    Stored PDFs:       ${pdf_count}"

    echo "═══════════════════════════════════════════"
    echo ""
}

# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════

echo ""
echo "═══════════════════════════════════════════"
echo "  Clinical Trial PDF Generator"
echo "═══════════════════════════════════════════"
echo "  Trials:             ${NUM_TRIALS}"
echo "  Patients per trial: ${PATIENTS_PER_TRIAL}"
echo "  Seed:               ${SEED}"
echo "  Wait for processor: ${WAIT_FOR_PROCESSING}"
echo "═══════════════════════════════════════════"
echo ""

# ── Pre-flight ──
preflight_checks
echo ""

# ── Record start time ──
START_TIME=$(date +%s)

# ── Run the generator container ──
log_info "Starting generator container..."
echo ""

docker compose -f "$COMPOSE_FILE" \
    run --rm \
    -e NUM_TRIALS="$NUM_TRIALS" \
    -e PATIENTS_PER_TRIAL="$PATIENTS_PER_TRIAL" \
    -e SEED="$SEED" \
    generator

GENERATOR_EXIT=$?
GENERATOR_ELAPSED=$(( $(date +%s) - START_TIME ))

echo ""
if [ $GENERATOR_EXIT -eq 0 ]; then
    log_ok "Generator completed successfully in ${GENERATOR_ELAPSED}s"
    log_ok "Generated ${NUM_TRIALS} trials → Kafka events published"
else
    log_error "Generator failed with exit code ${GENERATOR_EXIT}"
    exit $GENERATOR_EXIT
fi

# ── Optionally wait for processor ──
if [ "$WAIT_FOR_PROCESSING" = true ]; then
    echo ""
    wait_for_processing
fi

# ── Show statistics ──
# Give the processor a few seconds to finish if we're not waiting
if [ "$WAIT_FOR_PROCESSING" = false ]; then
    log_info "Giving processor 10s to start processing..."
    sleep 10
fi

show_statistics

TOTAL_ELAPSED=$(( $(date +%s) - START_TIME ))
log_ok "Total elapsed time: ${TOTAL_ELAPSED}s"

# ── Helpful next steps ──
echo ""
echo "Next steps:"
echo "  • Watch processing:    docker compose logs -f processor"
echo "  • Browse PDFs:         http://localhost:9001  (minioadmin/minioadmin123)"
echo "  • Explore graph:       http://localhost:7474  (neo4j/neo4jpassword)"
echo "  • Check vectors:       http://localhost:6333/dashboard"
echo "  • Kafka events:        http://localhost:8080  (start with --profile monitoring)"
echo ""