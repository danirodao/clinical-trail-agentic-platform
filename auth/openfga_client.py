"""
OpenFGA Python client wrapper.
Handles store connection, authorization checks, tuple management,
and ListObjects queries for computing access profiles.
"""

import os
import logging
import asyncio
import time
from typing import Optional
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

OPENFGA_API_URL = os.environ.get("OPENFGA_API_URL", "http://openfga:8080")
OPENFGA_STORE_ID = os.environ.get("OPENFGA_STORE_ID", "")
FAIL_CLOSED = os.environ.get("OPENFGA_FAIL_CLOSED", "true").lower() == "true"
CHECK_TIMEOUT = float(os.environ.get("OPENFGA_CHECK_TIMEOUT", "2.0"))
OPENFGA_CACHE_TTL_SECONDS = float(os.environ.get("OPENFGA_CACHE_TTL_SECONDS", "30"))
OPENFGA_CACHE_MAX_ENTRIES = int(os.environ.get("OPENFGA_CACHE_MAX_ENTRIES", "5000"))


@dataclass
class CheckResult:
    allowed: bool
    resolution_metadata: Optional[dict] = None
    error: Optional[str] = None
    # True when OpenFGA could not evaluate a condition due to missing context.
    # The caller MUST treat this as DENY — never as ALLOW.
    context_incomplete: bool = False


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
        self._cache_ttl = OPENFGA_CACHE_TTL_SECONDS
        self._cache_max_entries = OPENFGA_CACHE_MAX_ENTRIES
        self._cache: dict[str, tuple[float, object]] = {}
        self._cache_lock = asyncio.Lock()

    async def _cache_get(self, key: str):
        if self._cache_ttl <= 0:
            return None
        async with self._cache_lock:
            item = self._cache.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at < time.monotonic():
                self._cache.pop(key, None)
                return None
            return value

    async def _cache_set(self, key: str, value: object):
        if self._cache_ttl <= 0:
            return
        async with self._cache_lock:
            if len(self._cache) >= self._cache_max_entries:
                oldest = next(iter(self._cache), None)
                if oldest is not None:
                    self._cache.pop(oldest, None)
            self._cache[key] = (time.monotonic() + self._cache_ttl, value)

    async def clear_cache(self):
        async with self._cache_lock:
            self._cache.clear()

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
        use_cache: bool = True,
    ) -> CheckResult:
        """
        Check if a user has a relation to an object (plain ReBAC — no context).
        Returns CheckResult with .allowed boolean.
        FAIL CLOSED: Returns denied if OpenFGA is unreachable.
        """
        cache_key = f"check:{user}|{relation}|{object}"
        if use_cache:
            cached = await self._cache_get(cache_key)
            if isinstance(cached, CheckResult):
                return cached

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
            result = CheckResult(
                allowed=data.get("allowed", False),
                resolution_metadata=data.get("resolution_metadata"),
            )
            if use_cache:
                await self._cache_set(cache_key, result)
            return result

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.error(f"OpenFGA unreachable: {e}")
            if FAIL_CLOSED:
                return CheckResult(allowed=False, error=f"Service unavailable: {e}")
            raise

        except httpx.HTTPStatusError as e:
            logger.error(f"OpenFGA check failed: {e.response.status_code} {e.response.text}")
            return CheckResult(allowed=False, error=str(e))

    async def check_with_context(
        self,
        user: str,
        relation: str,
        object: str,
        context: dict,
    ) -> CheckResult:
        """
        ABAC/PBAC check — evaluates conditional tuples by passing dynamic
        attributes in the /check "context" payload.

        Context must contain all DYNAMIC attributes assembled by
        OpenFGAContextBuilder:
          current_time, requested_region, requested_area, requested_phase,
          stated_purpose, user_clearance_level, actual_cohort_size

        Security rules enforced here:
          - Results are NEVER cached: context attributes are per-request
            runtime values; caching would leak one user's context to another.
          - CONDITIONAL_RESULT (missing context) is treated as DENY.
          - Any error returns DENY (fail closed).

        Use check() for plain ReBAC relations without conditions.
        """
        if not context:
            # Refuse to call OpenFGA with an empty context — the condition
            # would evaluate to false anyway, but logging makes the bug visible.
            logger.error(
                "check_with_context called with empty context for "
                f"{user}|{relation}|{object} — treating as DENY"
            )
            return CheckResult(
                allowed=False,
                error="Empty context passed to check_with_context",
                context_incomplete=True,
            )

        try:
            client = await self._get_client()
            resp = await client.post(
                "/check",
                json={
                    "tuple_key": {
                        "user": user,
                        "relation": relation,
                        "object": object,
                    },
                    # Dynamic ABAC/PBAC attributes evaluated against the
                    # condition expression stored in the authorization model.
                    "context": context,
                }
            )

            # OpenFGA returns 400 with "missing_parameters" when context
            # keys required by the condition are absent.  Treat as DENY.
            if resp.status_code == 400:
                body = resp.json()
                code = body.get("code", "")
                msg  = body.get("message", "")
                logger.warning(
                    f"OpenFGA check returned 400 (possible missing context) "
                    f"for {user}|{relation}|{object}: [{code}] {msg}"
                )
                return CheckResult(
                    allowed=False,
                    error=msg,
                    context_incomplete=("missing" in msg.lower() or "parameter" in msg.lower()),
                )

            resp.raise_for_status()
            data = resp.json()

            # Detect CONDITIONAL_RESULT: OpenFGA resolved to a conditional node
            # but could not evaluate it (missing context field in the payload).
            # The API currently signals this via resolution_metadata; treat DENY.
            resolution = data.get("resolution_metadata", {})
            if resolution.get("cycle_detected") or resolution.get("datastore_query_count", 0) == 0:
                # Sanity sentinel: zero DB queries usually means short-circuit deny
                pass  # allowed=False is the right outcome; fall through

            result = CheckResult(
                allowed=data.get("allowed", False),
                resolution_metadata=resolution,
            )
            return result

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.error(f"OpenFGA unreachable during context check: {e}")
            # FAIL CLOSED — a connectivity issue must never grant access
            return CheckResult(allowed=False, error=f"Service unavailable: {e}")

        except httpx.HTTPStatusError as e:
            logger.error(
                f"OpenFGA context check failed: "
                f"{e.response.status_code} {e.response.text}"
            )
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
        use_cache: bool = True,
    ) -> list[str]:
        """
        List all objects of a type that a user has a relation to.
        Used to compute access profiles (e.g., all trials a user can view).
        """
        cache_key = f"list:{user}|{relation}|{object_type}"
        if use_cache:
            cached = await self._cache_get(cache_key)
            if isinstance(cached, list):
                return cached

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
            ids = [obj.split(":", 1)[1] if ":" in obj else obj for obj in objects]
            if use_cache:
                await self._cache_set(cache_key, ids)
            return ids

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.error(f"OpenFGA list-objects failed: {e}")
            if FAIL_CLOSED:
                return []
            raise
        except httpx.HTTPStatusError as e:
            # Fail-closed for API-level validation errors as well, and log
            # enough detail to diagnose malformed tuple keys or model mismatch.
            status = e.response.status_code if e.response is not None else "unknown"
            body = e.response.text if e.response is not None else ""
            logger.error(
                "OpenFGA list-objects returned HTTP error: user=%s relation=%s type=%s status=%s body=%s",
                user,
                relation,
                object_type,
                status,
                body,
            )
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
        
        Idempotent: Treats "already exists" as success (tuple was already there).
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
                await self.clear_cache()
                return True
            elif resp.status_code == 400:
                # Check if error is "tuple already exists" (idempotent)
                try:
                    error_resp = resp.json()
                    error_msg = error_resp.get("message", "")
                    if "already exists" in error_msg.lower():
                        logger.info(f"Tuple already exists (idempotent): {error_msg}")
                        await self.clear_cache()
                        return True  # Treat as success
                except:
                    pass
                logger.error(f"Tuple write failed: {resp.status_code} {resp.text}")
                return False
            else:
                logger.error(f"Tuple write failed: {resp.status_code} {resp.text}")
                return False

        except Exception as e:
            logger.error(f"Tuple write error: {e}")
            return False

    async def write_conditional_tuples(self, tuples: list[dict]) -> bool:
        """
        Write conditional relationship tuples that carry ABAC/PBAC attributes.

        Each entry must be a dict with:
          - user:     str   e.g. "organization:org-pharma-corp"
          - relation: str   e.g. "granted_org"
          - object:   str   e.g. "clinical_trial:ct-uuid"
          - condition_name: str  e.g. "check_fine_grained_access"
          - condition_context: dict  all STATIC attribute values, e.g.:
              {
                "valid_from":              "2026-01-01T00:00:00Z",
                "valid_until":             "2026-12-31T23:59:59Z",
                "permitted_regions":       ["EU", "NA"],
                "permitted_areas":         ["oncology"],
                "permitted_phases":        ["II", "III"],
                "approved_purposes":       ["study_ONCO_2026"],
                "resource_classification": 3,
                "minimum_cohort_size":     5,
              }

        The condition_context is stored on the tuple at write time (STATIC).
        Dynamic attributes are NEVER stored here — they are passed at /check time.

        Idempotent: Treats "already exists" as success.
        """
        tuple_keys = []
        for t in tuples:
            entry: dict = {
                "user":     t["user"],
                "relation": t["relation"],
                "object":   t["object"],
            }
            if "condition_name" in t:
                entry["condition"] = {
                    "name":    t["condition_name"],
                    # context here holds only STATIC attributes set by the
                    # governance approver at write time — never dynamic values
                    "context": t.get("condition_context", {}),
                }
            tuple_keys.append(entry)

        try:
            client = await self._get_client()
            resp = await client.post(
                "/write",
                json={"writes": {"tuple_keys": tuple_keys}}
            )
            if resp.status_code == 200:
                logger.info(f"Wrote {len(tuples)} conditional tuples to OpenFGA")
                await self.clear_cache()
                return True
            elif resp.status_code == 400:
                try:
                    error_resp = resp.json()
                    error_msg = error_resp.get("message", "")
                    if "already exists" in error_msg.lower():
                        logger.info(
                            f"Conditional tuple already exists (idempotent): {error_msg}"
                        )
                        await self.clear_cache()
                        return True
                except Exception:
                    pass
                logger.error(
                    f"Conditional tuple write failed: {resp.status_code} {resp.text}"
                )
                return False
            else:
                logger.error(
                    f"Conditional tuple write failed: {resp.status_code} {resp.text}"
                )
                return False

        except Exception as e:
            logger.error(f"Conditional tuple write error: {e}")
            return False

    async def read_tuple_conditions(
        self,
        user: str,
        relation: str,
        object: str,
    ) -> Optional[dict]:
        """
        Read the condition context (STATIC attributes) stored on a specific tuple.

        Returns the condition dict:
          {
            "name":    "check_fine_grained_access",
            "context": { ...static attributes... }
          }
        or None if the tuple does not exist or carries no condition.

        Used by CeilingValidator to fetch Tier 1 (ceiling) attributes before
        writing a Tier 2 (delegation) tuple.
        """
        try:
            client = await self._get_client()
            resp = await client.post(
                "/read",
                json={
                    "tuple_key": {
                        "user":     user,
                        "relation": relation,
                        "object":   object,
                    }
                }
            )
            resp.raise_for_status()
            data = resp.json()
            tuples = data.get("tuples", [])
            if not tuples:
                return None
            # Return the condition from the first matching tuple
            key = tuples[0].get("key", {})
            return key.get("condition")  # None if no condition on this tuple

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.error(f"OpenFGA read failed (tuple conditions): {e}")
            return None
        except httpx.HTTPStatusError as e:
            logger.error(
                f"OpenFGA read failed: {e.response.status_code} {e.response.text}"
            )
            return None

    async def delete_tuples(self, tuples: list[dict]) -> bool:
        """Delete relationship tuples (for revocation).
        
        Idempotent: Treats "tuple not found" as success (tuple was already gone).
        """
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
            if resp.status_code == 200:
                logger.info(f"Deleted {len(tuples)} tuples from OpenFGA")
                await self.clear_cache()
                return True
            elif resp.status_code == 400:
                # OpenFGA returns 400 when a tuple to delete did not exist.
                # That is an idempotent success — the end state is identical.
                try:
                    error_resp = resp.json()
                    error_msg = error_resp.get("message", "")
                    # Match only the specific "did not exist" variant; avoid
                    # the overly broad "not found" phrase which also appears in
                    # unrelated OpenFGA errors (e.g. store not found).
                    if "did not exist" in error_msg.lower():
                        logger.debug(
                            "OpenFGA delete skipped — tuple already absent (idempotent). "
                            "tuples=%d",
                            len(tuples),
                        )
                        await self.clear_cache()
                        return True
                except Exception:
                    pass
                logger.error(
                    "Tuple delete failed: status=%s body=%s", resp.status_code, resp.text
                )
                return False
            else:
                logger.error(f"Tuple delete failed: {resp.status_code} {resp.text}")
                return False

        except Exception as e:
            logger.error(f"Tuple delete error: {e}")
            return False

    async def tuple_exists(self, user: str, relation: str, object: str) -> bool:
        """
        Check if a tuple key exists in OpenFGA (conditional or plain).

        Uses /read on the exact tuple key instead of /check, because /check
        requires dynamic context for conditional tuples and can return false
        even when the tuple is present.
        """
        try:
            client = await self._get_client()
            resp = await client.post(
                "/read",
                json={
                    "tuple_key": {
                        "user": user,
                        "relation": relation,
                        "object": object,
                    }
                },
            )
            resp.raise_for_status()
            tuples = resp.json().get("tuples", [])
            return len(tuples) > 0
        except Exception as e:
            logger.error(f"Tuple existence read failed: {e}")
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