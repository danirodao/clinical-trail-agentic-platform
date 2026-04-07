"""
Phase 2 standalone integration test — runs INSIDE the Docker api container.

Usage:
    docker compose exec -w /app/api api python -m agent.test_agent
    docker compose exec -w /app/api api python -m agent.test_agent "custom query"
    docker compose exec -w /app/api -e TEST_RESEARCHER=researcher-dani api python -m agent.test_agent
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# AccessProfile dataclasses (mirrors auth/authorization_service.py)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CohortScope:
    cohort_id: str
    cohort_name: str
    filter_criteria: dict = field(default_factory=dict)


@dataclass
class TrialAccessScope:
    trial_id: str
    access_level: str   # 'individual' | 'aggregate'
    cohort_scopes: list[CohortScope] = field(default_factory=list)

    @property
    def has_patient_filter(self) -> bool:
        return len(self.cohort_scopes) > 0

    @property
    def is_unrestricted(self) -> bool:
        return len(self.cohort_scopes) == 0


@dataclass
class AccessProfile:
    user_id: str
    role: str
    organization_id: str
    allowed_trial_ids: list[str] = field(default_factory=list)
    aggregate_trial_ids: list[str] = field(default_factory=list)
    individual_trial_ids: list[str] = field(default_factory=list)
    trial_scopes: dict[str, TrialAccessScope] = field(default_factory=dict)
    has_any_access: bool = False
    has_individual_access: bool = False
    aggregate_only: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# Real data loader — matches actual PostgreSQL schema
# ─────────────────────────────────────────────────────────────────────────────

async def load_real_access_profile(
    researcher_username: str,
    organization_id: str,
) -> tuple[AccessProfile, dict]:
    """
    Build an AccessProfile by querying the real database.

    Schema facts (confirmed from introspection):
      - cohort.name            (NOT cohort_name)
      - cohort.filter_criteria (JSONB — trial_ids live HERE as filter_criteria->trial_ids)
      - researcher_assignment.is_active (real boolean column)
      - researcher_assignment.trial_id  (NULL for cohort-based assignments)
      - cohort_trial table exists but trial membership is authoritative in filter_criteria
    """
    import asyncpg

    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://ctuser:ctpassword@postgres:5432/clinical_trials",
    )

    print(f"\n📡 Connecting to PostgreSQL at {db_url.split('@')[1]}")
    conn = await asyncpg.connect(db_url)

    try:
        # ── 1. Direct trial assignments (trial_id IS NOT NULL) ────────────────
        direct_rows = await conn.fetch("""
            SELECT ra.trial_id::text,
                   ra.access_level
            FROM   researcher_assignment ra
            WHERE  ra.researcher_id    = $1
              AND  ra.organization_id  = $2
              AND  ra.trial_id IS NOT NULL
              AND  ra.cohort_id IS NULL
              AND  ra.is_active = true
        """, researcher_username, organization_id)

        # ── 2. Cohort-based assignments (cohort_id IS NOT NULL) ───────────────
        #    Trial IDs come from cohort.filter_criteria->>'trial_ids' (JSONB array)
        #    Column is `c.name` — NOT c.cohort_name
        cohort_rows = await conn.fetch("""
            SELECT ra.cohort_id::text,
                   ra.access_level,
                   c.name            AS cohort_name,
                   c.filter_criteria
            FROM   researcher_assignment ra
            JOIN   cohort c ON c.cohort_id = ra.cohort_id
            WHERE  ra.researcher_id   = $1
              AND  ra.organization_id = $2
              AND  ra.cohort_id IS NOT NULL
              AND  ra.is_active = true
        """, researcher_username, organization_id)

        # ── 3. Expand cohort rows → one row per trial ─────────────────────────
        #    filter_criteria.trial_ids is a JSON array of UUID strings
        cohort_trial_rows: list[dict] = []
        for row in cohort_rows:
            criteria = row["filter_criteria"] or {}
            if isinstance(criteria, str):
                criteria = json.loads(criteria)

            trial_ids_in_cohort: list[str] = criteria.get("trial_ids", [])

            # Fallback: if filter_criteria has no trial_ids, check cohort_trial table
            if not trial_ids_in_cohort:
                ct_rows = await conn.fetch("""
                    SELECT trial_id::text FROM cohort_trial
                    WHERE cohort_id = $1
                """, row["cohort_id"])
                trial_ids_in_cohort = [r["trial_id"] for r in ct_rows]

            for tid in trial_ids_in_cohort:
                cohort_trial_rows.append({
                    "cohort_id":    row["cohort_id"],
                    "cohort_name":  row["cohort_name"],
                    "access_level": row["access_level"],
                    "filter_criteria": criteria,
                    "trial_id":     tid,
                })

        # ── 4. Collect all trial IDs ──────────────────────────────────────────
        direct_trial_ids = {r["trial_id"] for r in direct_rows}
        cohort_trial_ids = {r["trial_id"] for r in cohort_trial_rows}
        all_trial_ids    = list(direct_trial_ids | cohort_trial_ids)

        print(f"   Direct assignments:  {len(direct_trial_ids)} trial(s)")
        print(f"   Cohort assignments:  {len(cohort_trial_ids)} trial(s) "
              f"across {len(cohort_rows)} cohort(s)")
        print(f"   Total unique trials: {len(all_trial_ids)}")

        if not all_trial_ids:
            print(f"\n⚠️  No active assignments found for {researcher_username}.")
            return AccessProfile(
                user_id=researcher_username,
                role="researcher",
                organization_id=organization_id,
            ), {}

        # ── 5. Fetch trial metadata for display ───────────────────────────────
        meta_rows = await conn.fetch("""
            SELECT trial_id::text, nct_id, title, phase, therapeutic_area
            FROM   clinical_trial
            WHERE  trial_id = ANY($1::uuid[])
        """, all_trial_ids)

        trial_meta: dict[str, dict] = {
            row["trial_id"]: {
                "nct_id":            row["nct_id"],
                "title":             row["title"],
                "phase":             row["phase"],
                "therapeutic_area":  row["therapeutic_area"],
            }
            for row in meta_rows
        }

        # ── 6. Build AccessProfile ────────────────────────────────────────────
        individual_ids: set[str] = set()
        aggregate_ids:  set[str] = set()

        for row in direct_rows:
            tid = row["trial_id"]
            if row["access_level"] == "individual":
                individual_ids.add(tid)
            else:
                aggregate_ids.add(tid)

        for row in cohort_trial_rows:
            tid = row["trial_id"]
            if row["access_level"] == "individual":
                individual_ids.add(tid)
            else:
                aggregate_ids.add(tid)

        # ── 7. Build per-trial scopes ─────────────────────────────────────────
        # Group cohort rows by trial_id
        cohorts_by_trial: dict[str, list[dict]] = {}
        for row in cohort_trial_rows:
            cohorts_by_trial.setdefault(row["trial_id"], []).append(row)

        trial_scopes: dict[str, TrialAccessScope] = {}
        for tid in all_trial_ids:
            access_level = "individual" if tid in individual_ids else "aggregate"
            scopes: list[CohortScope] = []

            # Cohort-based access → attach cohort filter
            if tid not in direct_trial_ids and tid in cohorts_by_trial:
                for ca in cohorts_by_trial[tid]:
                    # Normalise filter_criteria — strip internal trial_ids
                    # (they are a routing mechanism, not a patient filter)
                    criteria = {
                        k: v for k, v in ca["filter_criteria"].items()
                        if k != "trial_ids" and v not in (None, [], "")
                    }
                    scopes.append(CohortScope(
                        cohort_id=ca["cohort_id"],
                        cohort_name=ca["cohort_name"],
                        filter_criteria=criteria,
                    ))

            trial_scopes[tid] = TrialAccessScope(
                trial_id=tid,
                access_level=access_level,
                cohort_scopes=scopes,
            )

        profile = AccessProfile(
            user_id=researcher_username,
            role="researcher",
            organization_id=organization_id,
            allowed_trial_ids=all_trial_ids,
            individual_trial_ids=list(individual_ids),
            aggregate_trial_ids=list(aggregate_ids),
            trial_scopes=trial_scopes,
            has_any_access=bool(all_trial_ids),
            has_individual_access=bool(individual_ids),
            aggregate_only=not bool(individual_ids),
        )

        return profile, trial_meta

    finally:
        await conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────

SEP  = "=" * 65
DASH = "-" * 65


def print_profile_summary(profile: AccessProfile, trial_meta: dict) -> None:
    print(f"\n{SEP}")
    print(f"  ACCESS PROFILE: {profile.user_id} @ {profile.organization_id}")
    print(SEP)
    print(f"  Total trials:    {len(profile.allowed_trial_ids)}")
    print(f"  Individual:      {len(profile.individual_trial_ids)}")
    print(f"  Aggregate-only:  {len(profile.aggregate_trial_ids)}")

    if profile.trial_scopes:
        print("\n  Trial breakdown:")
        for tid, scope in profile.trial_scopes.items():
            meta  = trial_meta.get(tid, {})
            nct   = meta.get("nct_id", tid[:8] + "…")
            title = meta.get("title", "Unknown")[:48]
            icon  = "👤" if scope.access_level == "individual" else "📊"
            cohort_note = ""
            if scope.cohort_scopes:
                names = ", ".join(cs.cohort_name for cs in scope.cohort_scopes)
                criteria_parts = []
                for cs in scope.cohort_scopes:
                    for k, v in cs.filter_criteria.items():
                        if v:
                            criteria_parts.append(f"{k}={v}")
                criteria_str = " | ".join(criteria_parts[:3])
                cohort_note = f"\n       └─ cohort: {names}"
                if criteria_str:
                    cohort_note += f" [{criteria_str}]"
            print(f"    {icon} {nct}: {title}{cohort_note}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Test runners
# ─────────────────────────────────────────────────────────────────────────────

async def run_query_test(
    service,
    request_class,
    label: str,
    query: str,
    profile: AccessProfile,
    trial_ids: Optional[list[str]] = None,
) -> bool:
    """Run one test case. Returns True on success."""
    print(f"\n{SEP}")
    print(f"  TEST: {label}")
    print(f"  QUERY: {query}")
    if trial_ids:
        print(f"  SCOPE: {trial_ids}")
    print(DASH)

    try:
        request  = request_class(query=query, trial_ids=trial_ids)
        response = await service.query(request, profile)

        # Answer
        preview = response.answer[:400]
        if len(response.answer) > 400:
            preview += "…"
        print(f"\n📝 ANSWER:\n{preview}")

        # Tool calls
        if response.tool_calls:
            print(f"\n🔧 TOOLS CALLED ({len(response.tool_calls)}):")
            for tc in response.tool_calls:
                icon = "✅" if tc.status == "success" else "❌"
                print(f"   {icon} {tc.tool} ({tc.duration_ms}ms) — {tc.result_summary}")
        else:
            print("\n🔧 No tool calls (guardrails may have short-circuited)")

        # Access metadata
        print(f"\n🔐 Access level applied: {response.access_level_applied}")
        if response.filters_applied:
            print(f"   Filters: {', '.join(response.filters_applied)}")

        # Performance
        m = response.metadata
        print(f"\n⚡ {m.duration_ms}ms | {m.model_used} | "
              f"{m.total_tokens} tokens | {m.iteration_count} iterations")

        if response.error:
            print(f"\n⚠️  Error field: {response.error}")

        return response.error is None

    except Exception as exc:
        print(f"\n💥 EXCEPTION: {exc}")
        import traceback
        traceback.print_exc()
        return False


async def run_streaming_test(
    service,
    request_class,
    query: str,
    profile: AccessProfile,
) -> None:
    print(f"\n{SEP}")
    print("  TEST: Streaming event sequence")
    print(f"  QUERY: {query}")
    print(DASH)

    request     = request_class(query=query)
    events_seen: list[str] = []

    try:
        print()
        async for event in service.query_stream(request, profile):
            etype = event.event
            events_seen.append(etype)

            if etype == "status":
                print(f"   📡 {event.data.get('message', '')}")
            elif etype == "tool_call":
                print(f"   🔧 calling: {event.data.get('tool', '')}")
            elif etype == "tool_result":
                d = event.data
                print(f"   ✅ result:  {d.get('tool','')} "
                      f"({d.get('duration_ms',0)}ms) — {d.get('summary','')}")
            elif etype == "answer_token":
                print(event.data.get("token", ""), end="", flush=True)
            elif etype == "complete":
                r = event.data
                print(f"\n   🏁 complete — {len(r.tool_calls)} tools, "
                      f"{r.metadata.duration_ms}ms")
            elif etype == "error":
                print(f"   ❌ error: {event.data.get('message','')}")

        print(f"\n   Sequence: {' → '.join(events_seen)}")

        required = {"status", "complete"}
        missing  = required - set(events_seen)
        if missing:
            print(f"   ⚠️  Missing events: {missing}")
        else:
            print("   ✅ Event sequence valid")

    except Exception as exc:
        print(f"\n💥 Streaming EXCEPTION: {exc}")
        import traceback
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    researcher   = os.getenv("TEST_RESEARCHER", "researcher-jane")
    org_id       = os.getenv("TEST_ORG_ID",     "org-pharma-corp")
    custom_query: Optional[str] = sys.argv[1] if len(sys.argv) > 1 else None

    print(f"\n{'#' * 65}")
    print("  PHASE 2 — AGENT CORE INTEGRATION TEST")
    print(f"  Researcher: {researcher}")
    print(f"  Org:        {org_id}")
    print(f"  MCP URL:    {os.getenv('MCP_SERVER_URL', 'http://mcp-server:8001/mcp/sse')}")
    print(f"{'#' * 65}")

    # ── Load real access profile ───────────────────────────────────────────────
    try:
        profile, trial_meta = await load_real_access_profile(researcher, org_id)
    except Exception as exc:
        print(f"\n❌ Failed to load access profile: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print_profile_summary(profile, trial_meta)

    if not profile.has_any_access:
        print(f"⚠️  {researcher} has no active trial assignments.")
        print("   Grant access via the Manager Dashboard first.")
        sys.exit(1)

    # ── Import agent (deferred — keeps DB connection error messages clean) ─────
    from agent.service import AgentService
    from agent.models import QueryRequest

    service = AgentService()

    # ── Custom query mode ──────────────────────────────────────────────────────
    if custom_query:
        await run_query_test(service, QueryRequest, "Custom Query", custom_query, profile)
        return

    # ── Standard test suite ────────────────────────────────────────────────────
    individual_ids = profile.individual_trial_ids
    aggregate_ids  = profile.aggregate_trial_ids
    first_individual = individual_ids[:1] if individual_ids else None
    first_aggregate  = aggregate_ids[:1]  if aggregate_ids  else None

    test_cases: list[tuple] = [
        (
            "1 — Simple count (all trials)",
            "How many patients are enrolled in my authorized trials?",
            None,
        ),
        (
            "2 — Count grouped by sex",
            "Break down the patient count by sex across all my trials.",
            None,
        ),
        (
            "3 — Adverse events by severity",
            "What are the most common adverse events across my trials? "
            "Show a breakdown by severity.",
            None,
        ),
        (
            "4 — Trial discovery by topic",
            "Tell me about the oncology or cancer trials I have access to.",
            None,
        ),
        (
            "5 — Document / outcome search",
            "What are the primary outcome measures in my trials?",
            None,
        ),
    ]

    if first_individual:
        test_cases.append((
            "6 — Demographics, single individual-access trial",
            "Show me the age and sex breakdown of patients in this trial.",
            first_individual,
        ))

    if first_aggregate:
        test_cases.append((
            "7 — Aggregate-only trial (statistics only)",
            "How many patients are in this trial and what is the completion rate?",
            first_aggregate,
        ))

    if len(profile.allowed_trial_ids) >= 2:
        test_cases.append((
            "8 — Cross-trial comparison (→ GPT-4o route)",
            "Compare adverse event rates and patient demographics "
            "across all my authorized trials.",
            None,
        ))

    # Access-denied guard test
    test_cases.append((
        "9 — Access denied (fake trial UUID)",
        "Show me patient data for this trial.",
        ["00000000-0000-0000-0000-000000000000"],
    ))

    passed = 0
    for label, query, scope in test_cases:
        ok = await run_query_test(service, QueryRequest, label, query, profile, scope)
        if ok:
            passed += 1

    # ── Streaming test ─────────────────────────────────────────────────────────
    await run_streaming_test(
        service,
        QueryRequest,
        "How many patients are enrolled across my trials?",
        profile,
    )

    # ── Summary ────────────────────────────────────────────────────────────────
    total = len(test_cases)
    print(f"\n{'#' * 65}")
    print(f"  PHASE 2 COMPLETE — {passed}/{total} tests passed")
    print(f"{'#' * 65}\n")


if __name__ == "__main__":
    asyncio.run(main())