import asyncio
import os
from auth.openfga_client import get_openfga_client

# Mock env vars if needed (adjust to match your docker-compose if they differ)
os.environ["OPENFGA_API_URL"] = "http://localhost:8081" # Local port mapped to 8080
os.environ["OPENFGA_STORE_ID"] = "01JK6G77A5H16BTV7XF26C6X20" # This needs to be the real store ID

async def main():
    client = get_openfga_client()
    user = "user:researcher-dani"
    
    print(f"Checking OpenFGA for {user}...")
    
    individual_ids = await client.get_accessible_trial_ids("researcher-dani", access_level="individual")
    print(f"Individual Trial IDs: {individual_ids}")
    
    aggregate_ids = await client.get_accessible_trial_ids("researcher-dani", access_level="aggregate")
    print(f"Aggregate Trial IDs: {aggregate_ids}")
    
    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
