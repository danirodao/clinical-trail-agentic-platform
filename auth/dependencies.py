"""
FastAPI dependency injection for authentication and authorization.
"""

from functools import wraps
from typing import Annotated

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from auth.middleware import verify_token, UserContext, security_scheme


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security_scheme)
) -> UserContext:
    """Dependency: Extract and verify current user from JWT."""
    return await verify_token(credentials)


# Type alias for cleaner route signatures
CurrentUser = Annotated[UserContext, Depends(get_current_user)]


def require_role(*allowed_roles: str):
    """
    Dependency factory: Require specific role(s).
    Usage: @router.get("/...", dependencies=[Depends(require_role("domain_owner"))])
    """
    async def role_checker(user: CurrentUser) -> UserContext:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{user.role}' not authorized. Required: {allowed_roles}"
            )
        return user
    return role_checker


# Pre-built role dependencies
RequireDomainOwner = Depends(require_role("domain_owner"))
RequireManager = Depends(require_role("manager"))
RequireResearcher = Depends(require_role("researcher"))
RequireManagerOrOwner = Depends(require_role("domain_owner", "manager"))
RequireAnyRole = Depends(require_role("domain_owner", "manager", "researcher"))