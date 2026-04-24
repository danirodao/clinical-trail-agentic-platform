#!/bin/bash
set -euo pipefail

echo "═══════════════════════════════════════════════════════════"
echo "  Clinical Trial Platform — Auth Bootstrap"
echo "═══════════════════════════════════════════════════════════"

# Wait for Keycloak
echo "[1/4] Waiting for Keycloak..."
until curl -sf http://localhost:9010/health/ready > /dev/null 2>&1; do
    echo "  Keycloak not ready, waiting..."
    sleep 5
done
echo "  ✓ Keycloak is ready"

# Check if realms exist
echo "  Checking realms..."
MASTER_REALM=$(curl -s http://localhost:8180/realms/master | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('realm',''))" 2>/dev/null)
CLINICAL_REALM=$(curl -s http://localhost:8180/realms/clinical-trials | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('realm',''))" 2>/dev/null)

if [ -z "$MASTER_REALM" ] || [ -z "$CLINICAL_REALM" ]; then
    echo "  ✘ Realms not found. Master: '$MASTER_REALM', Clinical: '$CLINICAL_REALM'"
    exit 1
fi
echo "  ✓ Both realms exist"

# Ensure SSL is disabled for Master and Clinical Trials realms (Dev Mode)
echo "  Configuring SSL requirements..."
docker exec keycloak /opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8180 --realm master --user admin --password admin > /dev/null 2>&1
docker exec keycloak /opt/keycloak/bin/kcadm.sh update realms/master -s sslRequired=NONE > /dev/null 2>&1
docker exec keycloak /opt/keycloak/bin/kcadm.sh update realms/clinical-trials -s sslRequired=NONE > /dev/null 2>&1
echo "  ✓ SSL disabled for Dev environment"

# Get Keycloak admin token
echo "[2/4] Getting Keycloak admin token..."
ADMIN_RESPONSE=$(curl -s -X POST \
    "http://localhost:8180/realms/master/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=admin&password=admin&grant_type=password&client_id=admin-cli")

echo "  Admin token response:"
echo "$ADMIN_RESPONSE" | jq . 2>/dev/null || echo "  (non-JSON response) $ADMIN_RESPONSE"

ADMIN_TOKEN=$(echo "$ADMIN_RESPONSE" | jq -r '.access_token // empty' 2>/dev/null)

if [ -z "$ADMIN_TOKEN" ]; then
    echo "  ✘ Failed to acquire Admin token."
    exit 1
fi
echo "  ✓ Admin token acquired"

# Test user login
echo "[3/4] Testing researcher login..."
RESEARCHER_RESPONSE=$(curl -s -X POST \
    "http://localhost:8180/realms/clinical-trials/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=researcher-jane&password=researcher123&grant_type=password&client_id=research-platform-api&client_secret=research-platform-secret")

echo "  Researcher token response:"
echo "$RESEARCHER_RESPONSE" | jq . 2>/dev/null || echo "  (non-JSON response) $RESEARCHER_RESPONSE"

RESEARCHER_TOKEN=$(echo "$RESEARCHER_RESPONSE" | jq -r '.access_token // empty' 2>/dev/null)

if [ -z "$RESEARCHER_TOKEN" ]; then
    echo "  ✘ Failed to acquire Researcher token."
    exit 1
fi
echo "  ✓ Researcher JWT acquired"

# Decode and verify claims
echo ""
echo "  JWT Claims (decoded):"
PAYLOAD=$(echo "$RESEARCHER_TOKEN" | cut -d'.' -f2)
# Add padding and decode
echo "$PAYLOAD" | python3 -c "import sys, base64, json; p=sys.stdin.read().strip(); print(json.dumps(json.loads(base64.urlsafe_b64decode(p + '=' * (-len(p) % 4))), indent=2))"

# Check OpenFGA
echo ""
echo "[4/4] Checking OpenFGA store..."
STORES_JSON=$(curl -s http://localhost:8082/stores)
echo "  OpenFGA response:"
echo "$STORES_JSON" | jq . 2>/dev/null || echo "  (non-JSON response) $STORES_JSON"
echo "$STORES_JSON" | jq -r '.stores[]? | "  Found store: \(.name) (\(.id))"' 2>/dev/null || true

# Auto-sync OPENFGA_STORE_ID in .env if it's stale or missing
LIVE_STORE_ID=$(echo "$STORES_JSON" | jq -r '.stores[]? | select(.name=="clinical-trials") | .id' 2>/dev/null || true)
if [ -n "$LIVE_STORE_ID" ]; then
    ENV_FILE=".env"
    if grep -q "OPENFGA_STORE_ID" "$ENV_FILE" 2>/dev/null; then
        CURRENT_ID=$(grep "OPENFGA_STORE_ID" "$ENV_FILE" | cut -d= -f2)
        if [ "$CURRENT_ID" != "$LIVE_STORE_ID" ]; then
            sed -i "s/^OPENFGA_STORE_ID=.*/OPENFGA_STORE_ID=$LIVE_STORE_ID/" "$ENV_FILE"
            echo "  ✓ Updated OPENFGA_STORE_ID in .env: $LIVE_STORE_ID"
        else
            echo "  ✓ OPENFGA_STORE_ID in .env is current: $LIVE_STORE_ID"
        fi
    else
        echo "OPENFGA_STORE_ID=$LIVE_STORE_ID" >> "$ENV_FILE"
        echo "  ✓ Added OPENFGA_STORE_ID to .env: $LIVE_STORE_ID"
    fi
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Auth stack is ready!"
echo ""
echo "  Keycloak Admin:   http://localhost:8180/admin"
echo "  OpenFGA Playground: http://localhost:3000/playground"
echo "  API Gateway:       http://localhost:8000/docs"
echo ""
echo "  Test with:"
echo "    # Use the token acquired earlier:"
echo "    TOKEN=\"$RESEARCHER_TOKEN\""
echo ""
echo "    # Query API:"
echo "    curl -H \"Authorization: Bearer \$TOKEN\" http://localhost:8000/api/v1/research/my-access"
echo "═══════════════════════════════════════════════════════════"

ACCESS_ROLE=$(curl -s -H "Authorization: Bearer $RESEARCHER_TOKEN" http://localhost:8000/api/v1/research/my-access)
echo "  Access role response:"
echo "$ACCESS_ROLE" | jq . 2>/dev/null || echo "  (non-JSON response) $ACCESS_ROLE"