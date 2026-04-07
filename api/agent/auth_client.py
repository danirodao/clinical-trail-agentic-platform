import os
import httpx
from fastapi import HTTPException

async def get_mcp_access_token() -> str:
    """Fetches a Client Credentials token from Keycloak for M2M communication."""
    keycloak_url = os.getenv("KEYCLOAK_URL", "http://keycloak:8180")
    client_id = os.getenv("KEYCLOAK_MCP_CLIENT_ID", "research-platform-api")
    client_secret = os.getenv("KEYCLOAK_MCP_CLIENT_SECRET", "research-platform-secret") # the secret from Keycloak

    token_url = f"{keycloak_url}/realms/clinical-trials/protocol/openid-connect/token"
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to acquire M2M token from Keycloak")
            
        return response.json()["access_token"]