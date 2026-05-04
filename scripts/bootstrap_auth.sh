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

# Ensure clearance_level mapper exists for API client
echo "  Ensuring clearance_level claim mapper..."
API_CLIENT_UUID=$(docker exec keycloak /opt/keycloak/bin/kcadm.sh get clients -r clinical-trials -q clientId=research-platform-api | jq -r '.[0].id // empty')
if [ -z "$API_CLIENT_UUID" ]; then
    echo "  ✘ Could not resolve Keycloak client UUID for research-platform-api"
    exit 1
fi

CLEARANCE_MAPPER_ID=$(docker exec keycloak /opt/keycloak/bin/kcadm.sh get "clients/$API_CLIENT_UUID/protocol-mappers/models" -r clinical-trials | jq -r '.[] | select(.name=="clearance-level-mapper") | .id' | head -n1)
if [ -z "$CLEARANCE_MAPPER_ID" ]; then
    docker exec keycloak /opt/keycloak/bin/kcadm.sh create "clients/$API_CLIENT_UUID/protocol-mappers/models" -r clinical-trials \
        -s name=clearance-level-mapper \
        -s protocol=openid-connect \
        -s protocolMapper=oidc-usermodel-attribute-mapper \
        -s 'config."user.attribute"=clearance_level' \
        -s 'config."claim.name"=clearance_level' \
        -s 'config."jsonType.label"=int' \
        -s 'config."id.token.claim"=true' \
        -s 'config."access.token.claim"=true' \
        -s 'config."userinfo.token.claim"=true' \
        -s 'config."multivalued"=false' > /dev/null
    echo "  ✓ Added mapper clearance-level-mapper"
else
    echo "  ✓ Mapper clearance-level-mapper already exists"
fi

# Ensure custom user attribute is declared in Keycloak User Profile
echo "  Ensuring clearance_level is defined in User Profile..."
USER_PROFILE_JSON=$(docker exec keycloak /opt/keycloak/bin/kcadm.sh get realms/clinical-trials/users/profile)
HAS_CLEARANCE_PROFILE_ATTR=$(echo "$USER_PROFILE_JSON" | jq -r 'any(.attributes[]?; .name == "clearance_level")')
if [ "$HAS_CLEARANCE_PROFILE_ATTR" != "true" ]; then
    UPDATED_PROFILE_JSON=$(echo "$USER_PROFILE_JSON" | jq '
        .attributes += [{
            "name":"clearance_level",
            "displayName":"Clearance Level",
            "permissions":{"view":["admin"],"edit":["admin"]},
            "validations":{"pattern":{"pattern":"^[1-5]$","error-message":"Must be an integer between 1 and 5"}}
        }]
    ')
    docker exec -i keycloak /opt/keycloak/bin/kcadm.sh update realms/clinical-trials/users/profile -f - > /dev/null <<< "$UPDATED_PROFILE_JSON"
    echo "  ✓ Added clearance_level to User Profile"
else
    echo "  ✓ User Profile already includes clearance_level"
fi

# Seed a safe default clearance level for users that don't have it yet
echo "  Seeding missing clearance_level user attributes..."
for USERNAME in data-admin pharma-manager researcher-jane researcher-dani biotech-manager researcher-bob; do
    USER_ID=$(docker exec keycloak /opt/keycloak/bin/kcadm.sh get users -r clinical-trials -q username="$USERNAME" | jq -r '.[0].id // empty')
    if [ -z "$USER_ID" ]; then
        continue
    fi

    CURRENT_LEVEL=$(docker exec keycloak /opt/keycloak/bin/kcadm.sh get "users/$USER_ID" -r clinical-trials | jq -r '.attributes.clearance_level[0] // empty')
    if [ -z "$CURRENT_LEVEL" ]; then
        docker exec keycloak /opt/keycloak/bin/kcadm.sh update "users/$USER_ID" -r clinical-trials -s 'attributes.clearance_level=["1"]' > /dev/null
        echo "    - $USERNAME: clearance_level set to 1"
    fi
done
echo "  ✓ clearance_level user attributes are configured"

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