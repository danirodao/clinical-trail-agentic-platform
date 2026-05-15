import asyncio
import os
from auth.openfga_client import get_openfga_client

os.environ["OPENFGA_API_URL"] = "http://openfga:8080"
os.environ["OPENFGA_STORE_ID"] = "01KN1RTVYXFRTV75XWY8BTC2PG"

async def main():
    client = get_openfga_client()
    user = "user:researcher-dani"
    tid = "737b18d6-0c36-4e76-9b1c-bfd45b015d1e"
    
    print(f"Checking OpenFGA for {user} on trial {tid}...")
    
    # Check without context
    res = await client.check(user, "can_view_individual", f"clinical_trial:{tid}")
    print(f"Check (no context): {res.allowed}")
    
    # Check with context
    context = {
        "current_time": "2026-05-15T20:30:00Z",
        "requested_region": "EU",
        "requested_area": "oncology",
        "requested_phase": "Phase 3",
        "stated_purpose": "study_ONCO_2026",
        "user_clearance_level": 3,
        "actual_cohort_size": 10
    }
    res_ctx = await client.check_with_context(user, "can_view_individual", f"clinical_trial:{tid}", context)
    print(f"Check (with context): {res_ctx.allowed}")
    if res_ctx.error:
        print(f"Error: {res_ctx.error}")

    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
