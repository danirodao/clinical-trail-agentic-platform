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

# Ensure SSL is disabled for Master and Clinical Trials realms (Dev Mode)
echo "  Configuring SSL requirements..."
docker exec keycloak /opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8180 --realm master --user admin --password admin > /dev/null 2>&1
docker exec keycloak /opt/keycloak/bin/kcadm.sh update realms/master -s sslRequired=NONE > /dev/null 2>&1
docker exec keycloak /opt/keycloak/bin/kcadm.sh update realms/clinical-trials -s sslRequired=NONE > /dev/null 2>&1
echo "  ✓ SSL disabled for Dev environment"

# Get Keycloak admin token
echo "[2/4] Getting Keycloak admin token..."
ADMIN_TOKEN=$(curl -s -X POST \
    "http://localhost:8180/realms/master/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=admin&password=admin&grant_type=password&client_id=admin-cli" \
    | jq -r '.access_token')

if [ "$ADMIN_TOKEN" == "null" ] || [ -z "$ADMIN_TOKEN" ]; then
    echo "  ✘ Failed to acquire Admin token. Check Keycloak logs."
    exit 1
fi
echo "  ✓ Admin token acquired"

# Test user login
echo "[3/4] Testing researcher login..."
RESEARCHER_TOKEN=$(curl -s -X POST \
    "http://localhost:8180/realms/clinical-trials/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=researcher-jane&password=researcher123&grant_type=password&client_id=research-platform-api&client_secret=research-platform-secret" \
    | jq -r '.access_token')

if [ "$RESEARCHER_TOKEN" == "null" ] || [ -z "$RESEARCHER_TOKEN" ]; then
    echo "  ✘ Failed to acquire Researcher token."
    exit 1
fi
echo "  ✓ Researcher JWT acquired"

# Decode and verify claims
echo ""
echo "  JWT Claims (decoded):"
PAYLOAD=$(echo "$RESEARCHER_TOKEN" | cut -d'.' -f2)
# Add padding and decode
echo "$PAYLOAD" | python3 -c "import sys, base64, json; p=sys.stdin.read(); print(json.dumps(json.loads(base64.urlsafe_b64decode(p + '=' * (4 - len(p) % 4))), indent=2))"

# Check OpenFGA
echo ""
echo "[4/4] Checking OpenFGA store..."
STORES_JSON=$(curl -s http://localhost:8082/stores)
echo "$STORES_JSON" | jq -r '.stores[] | "  Found store: \(.name) (\(.id))"'

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

ACCESS_ROLE=$(curl -s -H "Authorization: Bearer $RESEARCHER_TOKEN" http://localhost:8000/api/v1/research/my-access | jq .)
echo "Access Role: $ACCESS_ROLE"