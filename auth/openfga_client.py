"""
OpenFGA Python client wrapper.
Handles store connection, authorization checks, tuple management,
and ListObjects queries for computing access profiles.
"""

import os
import logging
from typing import Optional
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

OPENFGA_API_URL = os.environ.get("OPENFGA_API_URL", "http://openfga:8080")
OPENFGA_STORE_ID = os.environ.get("OPENFGA_STORE_ID", "")
FAIL_CLOSED = os.environ.get("OPENFGA_FAIL_CLOSED", "true").lower() == "true"
CHECK_TIMEOUT = float(os.environ.get("OPENFGA_CHECK_TIMEOUT", "2.0"))


@dataclass
class CheckResult:
    allowed: bool
    resolution_metadata: Optional[dict] = None
    error: Optional[str] = None


class OpenFGAClient:
    """Async client for OpenFGA authorization operations."""

    def __init__(
        self,
        api_url: str = OPENFGA_API_URL,
        store_id: str = OPENFGA_STORE_ID,
    ):
        self.api_url = api_url
        self.store_id = store_id
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=f"{self.api_url}/stores/{self.store_id}",
                timeout=CHECK_TIMEOUT,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ─── Authorization Checks ─────────────────────────────────

    async def check(
        self,
        user: str,
        relation: str,
        object: str,
    ) -> CheckResult:
        """
        Check if a user has a relation to an object.
        Returns CheckResult with .allowed boolean.
        FAIL CLOSED: Returns denied if OpenFGA is unreachable.
        """
        try:
            client = await self._get_client()
            resp = await client.post(
                "/check",
                json={
                    "tuple_key": {
                        "user": user,
                        "relation": relation,
                        "object": object,
                    }
                }
            )
            resp.raise_for_status()
            data = resp.json()
            return CheckResult(
                allowed=data.get("allowed", False),
                resolution_metadata=data.get("resolution_metadata"),
            )

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.error(f"OpenFGA unreachable: {e}")
            if FAIL_CLOSED:
                return CheckResult(allowed=False, error=f"Service unavailable: {e}")
            raise

        except httpx.HTTPStatusError as e:
            logger.error(f"OpenFGA check failed: {e.response.status_code} {e.response.text}")
            return CheckResult(allowed=False, error=str(e))

    async def check_can_view_individual(self, user_id: str, trial_id: str) -> bool:
        """Check if user can view individual patient data for a trial."""
        result = await self.check(
            user=f"user:{user_id}",
            relation="can_view_individual",
            object=f"clinical_trial:{trial_id}",
        )
        return result.allowed

    async def check_can_view_aggregate(self, user_id: str, trial_id: str) -> bool:
        """Check if user can view aggregate data for a trial."""
        result = await self.check(
            user=f"user:{user_id}",
            relation="can_view_aggregate",
            object=f"clinical_trial:{trial_id}",
        )
        return result.allowed

    async def check_patient_access(self, user_id: str, patient_id: str) -> bool:
        """Check if user can view individual patient data (derived through trial enrollment)."""
        result = await self.check(
            user=f"user:{user_id}",
            relation="can_view_individual",
            object=f"patient:{patient_id}",
        )
        return result.allowed

    # ─── List Objects (for access profiles) ───────────────────

    async def list_objects(
        self,
        user: str,
        relation: str,
        object_type: str,
    ) -> list[str]:
        """
        List all objects of a type that a user has a relation to.
        Used to compute access profiles (e.g., all trials a user can view).
        """
        try:
            client = await self._get_client()
            resp = await client.post(
                "/list-objects",
                json={
                    "user": user,
                    "relation": relation,
                    "type": object_type,
                }
            )
            resp.raise_for_status()
            data = resp.json()
            # Returns list like ["clinical_trial:uuid1", "clinical_trial:uuid2"]
            objects = data.get("objects", [])
            # Strip the type prefix to return just IDs
            return [obj.split(":", 1)[1] if ":" in obj else obj for obj in objects]

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.error(f"OpenFGA list-objects failed: {e}")
            if FAIL_CLOSED:
                return []
            raise

    async def get_accessible_trial_ids(
        self, user_id: str, access_level: str = "aggregate"
    ) -> list[str]:
        """Get all trial IDs a user can access at the specified level."""
        relation = "can_view_individual" if access_level == "individual" else "can_view_aggregate"
        return await self.list_objects(
            user=f"user:{user_id}",
            relation=relation,
            object_type="clinical_trial",
        )

    # ─── Tuple Management ─────────────────────────────────────

    async def write_tuples(self, tuples: list[dict]) -> bool:
        """
        Write relationship tuples.
        Each tuple: {"user": "user:X", "relation": "R", "object": "type:Y"}
        """
        try:
            client = await self._get_client()
            resp = await client.post(
                "/write",
                json={
                    "writes": {
                        "tuple_keys": [
                            {
                                "user": t["user"],
                                "relation": t["relation"],
                                "object": t["object"],
                            }
                            for t in tuples
                        ]
                    }
                }
            )
            if resp.status_code == 200:
                logger.info(f"Wrote {len(tuples)} tuples to OpenFGA")
                return True
            else:
                logger.error(f"Tuple write failed: {resp.status_code} {resp.text}")
                return False

        except Exception as e:
            logger.error(f"Tuple write error: {e}")
            return False

    async def delete_tuples(self, tuples: list[dict]) -> bool:
        """Delete relationship tuples (for revocation)."""
        try:
            client = await self._get_client()
            resp = await client.post(
                "/write",
                json={
                    "deletes": {
                        "tuple_keys": [
                            {
                                "user": t["user"],
                                "relation": t["relation"],
                                "object": t["object"],
                            }
                            for t in tuples
                        ]
                    }
                }
            )
            return resp.status_code == 200

        except Exception as e:
            logger.error(f"Tuple delete error: {e}")
            return False

    # ─── Convenience: Grant org access to trial ───────────────

    async def grant_org_trial_access(self, org_id: str, trial_id: str) -> bool:
        """Domain owner approves organization access to a trial."""
        return await self.write_tuples([
            {
                "user": f"organization:{org_id}",
                "relation": "granted_org",
                "object": f"clinical_trial:{trial_id}",
            }
        ])

    async def assign_researcher_to_trial(self, user_id: str, trial_id: str) -> bool:
        """Manager assigns a researcher to a specific trial (individual access)."""
        return await self.write_tuples([
            {
                "user": f"user:{user_id}",
                "relation": "assigned_researcher",
                "object": f"clinical_trial:{trial_id}",
            }
        ])

    async def register_patient_enrollment(self, patient_id: str, trial_id: str) -> bool:
        """Register that a patient is enrolled in a trial (for derived access)."""
        return await self.write_tuples([
            {
                "user": f"clinical_trial:{trial_id}",
                "relation": "enrolled_in_trial",
                "object": f"patient:{patient_id}",
            }
        ])

    async def revoke_org_trial_access(self, org_id: str, trial_id: str) -> bool:
        """Revoke organization access to a trial."""
        return await self.delete_tuples([
            {
                "user": f"organization:{org_id}",
                "relation": "granted_org",
                "object": f"clinical_trial:{trial_id}",
            }
        ])


# ─── Singleton ────────────────────────────────────────────────

_fga_client: Optional[OpenFGAClient] = None


def get_openfga_client() -> OpenFGAClient:
    global _fga_client
    if _fga_client is None:
        _fga_client = OpenFGAClient()
    return _fga_client