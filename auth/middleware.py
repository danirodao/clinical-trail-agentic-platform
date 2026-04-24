"""
JWT verification middleware for FastAPI.
Validates Keycloak-issued JWTs and extracts user context.
"""

import os
import time
from typing import Optional

import httpx
from jose import jwt, jwk, JWTError
from jose.utils import base64url_decode
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

# Cache JWKS keys
_jwks_cache: dict = {}
_jwks_cache_expiry: float = 0
JWKS_CACHE_TTL = 300  # 5 minutes


class UserContext(BaseModel):
    """Extracted from JWT claims. Available on every authenticated request."""
    user_id: str                # Keycloak 'sub' claim
    username: str               # Keycloak 'preferred_username'
    email: Optional[str] = None
    role: str                   # Primary role: domain_owner | manager | researcher
    organization_id: str        # Custom claim from Keycloak attribute mapper
    organization_name: Optional[str] = None
    realm_roles: list[str] = []  # All realm roles


KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://keycloak:8180")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "clinical-trials")
KEYCLOAK_CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "research-platform-api")
ISSUER_URL = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
JWKS_URL = f"{ISSUER_URL}/protocol/openid-connect/certs"

security_scheme = HTTPBearer()


async def _get_jwks() -> dict:
    """Fetch and cache JWKS from Keycloak."""
    global _jwks_cache, _jwks_cache_expiry

    if _jwks_cache and time.time() < _jwks_cache_expiry:
        return _jwks_cache

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(JWKS_URL)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_cache_expiry = time.time() + JWKS_CACHE_TTL

    return _jwks_cache


def _get_signing_key(jwks: dict, token: str) -> str:
    """Extract the correct signing key from JWKS based on token header kid."""
    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header.get("kid")

    for key in jwks.get("keys", []):
        if key["kid"] == kid:
            return key

    raise HTTPException(status_code=401, detail="Signing key not found")


async def verify_token(credentials: HTTPAuthorizationCredentials) -> UserContext:
    """Verify JWT and extract UserContext."""
    token = credentials.credentials

    try:
        jwks = await _get_jwks()
        signing_key = _get_signing_key(jwks, token)

        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience="account",  # Keycloak default audience
            issuer=ISSUER_URL,
            options={
                "verify_aud": False,  # Keycloak doesn't always set aud to client_id
                "verify_iss": False,  # Disabled for dev (localhost vs keycloak:8180)
                "verify_exp": True,
                "verify_iat": True,
            }
        )

        # Extract realm roles from the JWT
        realm_roles = payload.get("realm_roles", [])
        # Fallback: check nested realm_access structure (Keycloak default)
        if not realm_roles:
            realm_access = payload.get("realm_access", {})
            realm_roles = realm_access.get("roles", [])

        # Determine primary role (priority: domain_owner > manager > researcher)
        role = "researcher"  # default
        if "domain_owner" in realm_roles:
            role = "domain_owner"
        elif "manager" in realm_roles:
            role = "manager"

        # Extract organization_id from custom claim
        organization_id = payload.get("organization_id", "")
        if not organization_id:
            raise HTTPException(
                status_code=403,
                detail="Token missing organization_id claim. Contact administrator."
            )

        subject = payload.get("sub")
        if not subject:
            raise HTTPException(
                status_code=401,
                detail="Invalid token: missing 'sub' claim. Request an OpenID Connect user access token."
            )

        return UserContext(
            user_id=subject,
            username=payload.get("preferred_username", "unknown"),
            email=payload.get("email"),
            role=role,
            organization_id=organization_id,
            organization_name=payload.get("organization_name"),
            realm_roles=realm_roles,
        )

    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Authentication service unavailable")