"""
Bootstrap OpenFGA: Create store, write authorization model, seed initial tuples.
Runs as an init container after OpenFGA is healthy.
"""

import json
import os
import sys
import time
import httpx

OPENFGA_API_URL = os.environ.get("OPENFGA_API_URL", "http://openfga:8080")
MAX_RETRIES = 10
RETRY_DELAY = 3


def wait_for_openfga(client: httpx.Client) -> None:
    """Wait until OpenFGA is ready."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.get(f"{OPENFGA_API_URL}/healthz")
            if resp.status_code == 200:
                print("[OK] OpenFGA is healthy")
                return
        except httpx.ConnectError:
            pass
        print(f"[WAIT] OpenFGA not ready (attempt {attempt + 1}/{MAX_RETRIES})")
        time.sleep(RETRY_DELAY)
    print("[FATAL] OpenFGA did not become healthy")
    sys.exit(1)


def create_store(client: httpx.Client) -> str:
    """Create an OpenFGA store and return the store ID."""
    # Check if store already exists
    resp = client.get(f"{OPENFGA_API_URL}/stores")
    resp.raise_for_status()
    stores = resp.json().get("stores", [])

    for store in stores:
        if store["name"] == "clinical-trials":
            store_id = store["id"]
            print(f"[OK] Store already exists: {store_id}")
            return store_id

    # Create new store
    resp = client.post(
        f"{OPENFGA_API_URL}/stores",
        json={"name": "clinical-trials"}
    )
    resp.raise_for_status()
    store_id = resp.json()["id"]
    print(f"[OK] Created store: {store_id}")
    return store_id


def write_authorization_model(client: httpx.Client, store_id: str) -> str:
    """Write the authorization model and return the model ID."""
    model_path = os.path.join(os.path.dirname(__file__), "model.json")

    with open(model_path) as f:
        model = json.load(f)

    resp = client.post(
        f"{OPENFGA_API_URL}/stores/{store_id}/authorization-models",
        json=model
    )
    resp.raise_for_status()
    model_id = resp.json()["authorization_model_id"]
    print(f"[OK] Wrote authorization model: {model_id}")
    return model_id

def read_existing_tuples(client: httpx.Client, store_id: str) -> set[tuple]:
    """Read all existing tuples and return as a set of (user, relation, object)."""
    existing = set()
    continuation_token = None

    while True:
        body = {}
        if continuation_token:
            body["continuation_token"] = continuation_token

        resp = client.post(
            f"{OPENFGA_API_URL}/stores/{store_id}/read",
            json=body
        )
        resp.raise_for_status()
        data = resp.json()

        for t in data.get("tuples", []):
            key = t["key"]
            existing.add((key["user"], key["relation"], key["object"]))

        continuation_token = data.get("continuation_token", "")
        if not continuation_token:
            break

    return existing
def write_seed_tuples(client: httpx.Client, store_id: str) -> None:
    """Seed initial relationship tuples for the demo users."""
    desired_tuples = [
        # ── Organization memberships ──
        # data-admin is a domain_owner (platform-level)
        {
            "user": "user:data-admin",
            "relation": "domain_owner",
            "object": "organization:org-platform"
        },
        {
            "user": "user:data-admin",
            "relation": "member",
            "object": "organization:org-platform"
        },
        # pharma-manager is a manager + member of PharmaCorp
        {
            "user": "user:pharma-manager",
            "relation": "manager",
            "object": "organization:org-pharma-corp"
        },
        {
            "user": "user:pharma-manager",
            "relation": "member",
            "object": "organization:org-pharma-corp"
        },
        # researcher-jane is a member of PharmaCorp
        {
            "user": "user:researcher-jane",
            "relation": "member",
            "object": "organization:org-pharma-corp"
        },
        # researcher-dani is an assigned_researcher of PharmaCorp
        {
            "user": "user:researcher-dani",
            "relation": "member",
            "object": "organization:org-pharma-corp"
        },
        # biotech-manager is a manager + member of BioTech Inc
        {
            "user": "user:biotech-manager",
            "relation": "manager",
            "object": "organization:org-biotech-inc"
        },
        {
            "user": "user:biotech-manager",
            "relation": "member",
            "object": "organization:org-biotech-inc"
        },
        # researcher-bob is a member of BioTech Inc
        {
            "user": "user:researcher-bob",
            "relation": "member",
            "object": "organization:org-biotech-inc"
        },
    ]

    # Read what already exists
    existing = read_existing_tuples(client, store_id)
    print(f"[INFO] Found {len(existing)} existing tuples")

    # Filter to only new tuples
    new_tuples = [
        t for t in desired_tuples
        if (t["user"], t["relation"], t["object"]) not in existing
    ]

    if not new_tuples:
        print(f"[OK] All {len(desired_tuples)} seed tuples already exist (idempotent)")
        return

    # Write only the missing tuples
    resp = client.post(
        f"{OPENFGA_API_URL}/stores/{store_id}/write",
        json={
            "writes": {"tuple_keys": new_tuples}
        }
    )

    if resp.status_code == 200:
        print(f"[OK] Wrote {len(new_tuples)} new tuples (skipped {len(desired_tuples) - len(new_tuples)} existing)")
    else:
        print(f"[ERROR] Tuple write failed: {resp.status_code} {resp.text}")
        sys.exit(1)



def main():
    print("=" * 60)
    print("OpenFGA Bootstrap — Clinical Trials Platform")
    print("=" * 60)

    with httpx.Client(timeout=30.0) as client:
        wait_for_openfga(client)
        store_id = create_store(client)
        model_id = write_authorization_model(client, store_id)
        write_seed_tuples(client, store_id)

    print("=" * 60)
    print(f"STORE_ID={store_id}")
    print(f"MODEL_ID={model_id}")
    print("Set OPENFGA_STORE_ID in your .env file!")
    print("=" * 60)


if __name__ == "__main__":
    main()