"""
Phase 1 Complete Tool Tests — direct function calls, no FastMCP context needed.
Run inside the mcp-server container:
    docker compose exec mcp-server python test_tools.py
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from db import postgres, qdrant_client, neo4j_client
from access_control import AccessContext
from utils import serialize_row, success_response, error_response

SEP = "=" * 60


def p(label: str, value) -> None:
    print(f"  {label}: {value}")


def section(title: str) -> None:
    print(f"\n{SEP}\nTEST: {title}\n{SEP}")


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

async def build_context() -> tuple[str, list[str], list[str]]:
    rows = await postgres.fetch(
        "SELECT trial_id, nct_id FROM clinical_trial LIMIT 4"
    )
    if not rows:
        print("ERROR: No trials found. Is data ingested?")
        sys.exit(1)

    trial_ids = [str(r["trial_id"]) for r in rows]
    nct_ids   = [r["nct_id"] for r in rows]

    ctx = {
        "user_id":           "researcher-jane",
        "role":              "researcher",
        "organization_id":   "org-pharma-corp",
        "allowed_trial_ids": trial_ids,
        "access_levels":     {tid: "individual" for tid in trial_ids},
        "patient_filters": {
            trial_ids[0]: [{
                "cohort_id":   "test-cohort-1",
                "cohort_name": "Hispanic CT",
                "criteria":    {"ethnicity": ["Hispanic or Latino"], "age_min": 10, "age_max": 100},
            }]
        },
    }

    print("Test context:")
    for tid, nct in zip(trial_ids, nct_ids):
        tag = "FILTERED" if tid in ctx["patient_filters"] else "unrestricted"
        print(f"  {nct} ({tid[:8]}…) → individual [{tag}]")

    return json.dumps(ctx), trial_ids, nct_ids


def agg_context(ctx_json: str) -> str:
    """Return a copy of the context where all trials are aggregate-only."""
    ctx = json.loads(ctx_json)
    ctx["access_levels"]  = {tid: "aggregate" for tid in ctx["allowed_trial_ids"]}
    ctx["patient_filters"] = {}
    return json.dumps(ctx)


# ---------------------------------------------------------------------------
# Direct implementations (mirrors MCP tool logic without FastMCP wrapper)
# ---------------------------------------------------------------------------

async def do_search_trials(query="", phase="", therapeutic_area="", access_context="") -> dict:
    ctx = AccessContext.from_json(access_context)
    if not ctx.allowed_trial_ids:
        return {"status": "error", "error": "No access"}

    results_by_id: dict = {}

    if query.strip():
        try:
            chunks = await qdrant_client.search_vectors(
                query_text=query.strip(),
                trial_ids=ctx.allowed_trial_ids,
                limit=20,
                score_threshold=0.25,
            )
            for c in chunks:
                tid = c["trial_id"]
                if tid not in results_by_id or c["score"] > results_by_id[tid].get("relevance_score", 0):
                    results_by_id[tid] = {
                        "trial_id": tid, "nct_id": c["nct_id"],
                        "relevance_score": c["score"], "_source": "semantic",
                        "matched_section": c["section"],
                    }
        except Exception as e:
            print(f"  [warn] Qdrant search: {e}")

    conds, params, idx = [], [], 1
    tf, tp, idx = ctx.build_trial_id_filter(ctx.allowed_trial_ids, idx, "ct.trial_id")
    conds.append(tf); params.extend(tp)
    if phase.strip():
        conds.append(f"ct.phase = ${idx}"); params.append(phase.strip()); idx += 1
    if therapeutic_area.strip():
        conds.append(f"LOWER(ct.therapeutic_area) LIKE LOWER(${idx})")
        params.append(f"%{therapeutic_area.strip()}%"); idx += 1

    sql = f"""
        SELECT ct.trial_id, ct.nct_id, ct.title, ct.phase,
               ct.therapeutic_area, ct.overall_status, ct.enrollment_count
        FROM clinical_trial ct WHERE {' AND '.join(conds)}
        ORDER BY ct.start_date DESC NULLS LAST LIMIT 20
    """
    rows = await postgres.fetch(sql, *params)
    seen = set(results_by_id.keys())
    for row in rows:
        tid = str(row["trial_id"])
        if tid not in seen:
            results_by_id[tid] = {**serialize_row(row), "relevance_score": None, "_source": "structured"}

    data = list(results_by_id.values())
    data.sort(key=lambda x: (0 if x.get("relevance_score") else 1, -(x.get("relevance_score") or 0)))
    return {"status": "success", "data": data, "metadata": {"total_found": len(data)}}


async def do_adverse_events(
    trial_ids="", severity="", serious_only="", ae_term="",
    group_by="", access_context=""
) -> dict:
    ctx = AccessContext.from_json(access_context)
    requested = [t.strip() for t in trial_ids.split(",") if t.strip()] if trial_ids else []
    authorized = ctx.validate_trial_access(requested)
    if not authorized:
        return {"status": "error", "error": "No access"}

    effective = ctx.get_effective_access_level(authorized)
    params, idx = [], 1
    aw, ap, idx = ctx.build_authorized_patient_filter(authorized, idx)
    params.extend(ap)

    extra = []
    if severity.strip():
        extra.append(f"ae.severity = ${idx}"); params.append(severity.strip()); idx += 1
    if serious_only.lower() == "true":
        extra.append("ae.serious = TRUE")
    if ae_term.strip():
        extra.append(f"LOWER(ae.ae_term) LIKE LOWER(${idx})")
        params.append(f"%{ae_term.strip()}%"); idx += 1

    where = " AND ".join([f"({aw})"] + extra)

    summary_sql = f"""
        SELECT COUNT(*) AS total_events,
               COUNT(DISTINCT ae.patient_id) AS patients_with_ae,
               COUNT(*) FILTER (WHERE ae.serious = TRUE) AS serious_count,
               COUNT(*) FILTER (WHERE ae.severity = 'Severe') AS severe_count
        FROM adverse_event ae
        JOIN patient p ON ae.patient_id = p.patient_id
        JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
        WHERE ae.trial_id = pte.trial_id AND {where}
    """
    summary_rows = await postgres.fetch(summary_sql, *params)
    summary = serialize_row(summary_rows[0]) if summary_rows else {}

    top_sql = f"""
        SELECT ae.ae_term, COUNT(*) AS count
        FROM adverse_event ae
        JOIN patient p ON ae.patient_id = p.patient_id
        JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
        WHERE ae.trial_id = pte.trial_id AND {where}
        GROUP BY ae.ae_term ORDER BY count DESC LIMIT 10
    """
    top_rows = await postgres.fetch(top_sql, *params)

    grouped = []
    if group_by.strip():
        VALID = {"severity": "ae.severity", "arm": "p.arm_assigned",
                 "serious": "ae.serious", "ae_term": "ae.ae_term"}
        expr = VALID.get(group_by.strip().lower())
        if expr:
            g_sql = f"""
                SELECT {expr} AS {group_by.strip().lower()}, COUNT(*) AS count
                FROM adverse_event ae
                JOIN patient p ON ae.patient_id = p.patient_id
                JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                WHERE ae.trial_id = pte.trial_id AND {where}
                GROUP BY {expr} ORDER BY count DESC
            """
            grouped = [serialize_row(r) for r in await postgres.fetch(g_sql, *params)]

    return {
        "status": "success",
        "data": {
            "summary": summary,
            "top_event_terms": [serialize_row(r) for r in top_rows],
            "grouped": grouped or None,
        },
        "metadata": {"trials_queried": len(authorized), "effective_access_level": effective},
    }


async def do_lab_results(trial_ids="", test_name="", access_context="") -> dict:
    ctx = AccessContext.from_json(access_context)
    requested = [t.strip() for t in trial_ids.split(",") if t.strip()] if trial_ids else []
    authorized = ctx.validate_trial_access(requested)
    if not authorized:
        return {"status": "error", "error": "No access"}

    params, idx = [], 1
    aw, ap, idx = ctx.build_authorized_patient_filter(authorized, idx)
    params.extend(ap)
    extra = []
    if test_name.strip():
        extra.append(f"LOWER(lr.test_name) LIKE LOWER(${idx})")
        params.append(f"%{test_name.strip()}%"); idx += 1
    where = " AND ".join([f"({aw})"] + extra)

    sql = f"""
        SELECT lr.test_name, lr.result_unit, COUNT(*) AS count,
               ROUND(AVG(lr.result_value)::numeric, 2) AS mean_value,
               MIN(lr.result_value) AS min_value, MAX(lr.result_value) AS max_value
        FROM lab_result lr
        JOIN patient p ON lr.patient_id = p.patient_id
        JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
        WHERE lr.trial_id = pte.trial_id AND {where}
        GROUP BY lr.test_name, lr.result_unit ORDER BY count DESC LIMIT 20
    """
    rows = await postgres.fetch(sql, *params)
    return {"status": "success", "data": {"test_statistics": [serialize_row(r) for r in rows]}}




async def do_concomitant_meds(trial_ids="", medication_name="", access_context="") -> dict:
    ctx = AccessContext.from_json(access_context)
    requested = [t.strip() for t in trial_ids.split(",") if t.strip()] if trial_ids else []
    authorized = ctx.validate_trial_access(requested)
    if not authorized:
        return {"status": "error", "error": "No access"}

    params, idx = [], 1
    aw, ap, idx = ctx.build_authorized_patient_filter(authorized, idx)
    params.extend(ap)
    extra = []
    if medication_name.strip():
        extra.append(f"LOWER(pm.medication_name) LIKE LOWER(${idx})")
        params.append(f"%{medication_name.strip()}%"); idx += 1
    where = " AND ".join([f"({aw})"] + extra)

    sql = f"""
        SELECT pm.medication_name, COUNT(*) AS count,
               COUNT(DISTINCT pm.patient_id) AS patient_count
        FROM patient_medication pm
        JOIN patient p ON pm.patient_id = p.patient_id
        JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
        WHERE {where}
        GROUP BY pm.medication_name ORDER BY patient_count DESC LIMIT 20
    """
    rows = await postgres.fetch(sql, *params)
    return {"status": "success", "data": {"medication_frequency": [serialize_row(r) for r in rows]}}


async def do_compare_arms(trial_id="", access_context="") -> dict:
    ctx = AccessContext.from_json(access_context)
    if not ctx.has_access(trial_id):
        return {"status": "error", "error": "No access"}

    params, idx = [], 1
    aw, ap, idx = ctx.build_authorized_patient_filter([trial_id], idx)
    params.extend(ap)

    demo_sql = f"""
        SELECT p.arm_assigned,
               COUNT(DISTINCT p.patient_id) AS patient_count,
               ROUND(AVG(p.age)::numeric, 1) AS avg_age,
               COUNT(*) FILTER (WHERE p.sex = 'M') AS male_count,
               COUNT(*) FILTER (WHERE p.sex = 'F') AS female_count
        FROM patient p
        JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
        WHERE {aw} GROUP BY p.arm_assigned
    """
    ae_sql = f"""
        SELECT p.arm_assigned, COUNT(*) AS total_events,
               COUNT(*) FILTER (WHERE ae.serious = TRUE) AS serious_events
        FROM adverse_event ae
        JOIN patient p ON ae.patient_id = p.patient_id
        JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
        WHERE ae.trial_id = pte.trial_id AND {aw} GROUP BY p.arm_assigned
    """
    demo_rows = await postgres.fetch(demo_sql, *params)
    ae_rows   = await postgres.fetch(ae_sql,   *params)

    return {
        "status": "success",
        "data": {
            "demographics_by_arm": [serialize_row(r) for r in demo_rows],
            "adverse_events_by_arm": [serialize_row(r) for r in ae_rows],
        },
    }





async def do_search_documents(query="", section="", access_context="") -> dict:
    ctx = AccessContext.from_json(access_context)
    if not ctx.allowed_trial_ids or not query.strip():
        return {"status": "error", "error": "No access or empty query"}

    try:
        chunks = await qdrant_client.search_vectors(
            query_text=query.strip(),
            trial_ids=ctx.allowed_trial_ids,
            limit=10,
            section=section.strip() or None,
            score_threshold=0.25,
        )
    except Exception as e:
        return {"status": "error", "error": str(e)}

    return {
        "status": "success",
        "data": {
            "total_found": len(chunks),
            "chunks": [
                {
                    "nct_id": c["nct_id"], "section": c["section"],
                    "score": c["score"],   "text": c["chunk_text"][:200],
                }
                for c in chunks
            ],
        },
    }


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

async def test_search_trials(ctx_json: str) -> None:
    section("search_trials — semantic + structured")
    r = await do_search_trials(query="melanoma immunotherapy", access_context=ctx_json)
    print(f"  Status: {r['status']}")
    if r["status"] == "success":
        p("total_found", r["metadata"]["total_found"])
        for t in r["data"][:3]:
            print(f"    {t.get('nct_id','?')}  score={t.get('relevance_score','N/A')}")
    else:
        print(f"  Error: {r.get('error')}")

    section("search_trials — structured only (phase filter)")
    r = await do_search_trials(phase="Phase 2", access_context=ctx_json)
    print(f"  Status: {r['status']}")
    if r["status"] == "success":
        for t in r["data"][:3]:
            print(f"    {t.get('nct_id','?')}  phase={t.get('phase')}")


async def test_adverse_events(ctx_json: str) -> None:
    section("get_adverse_events — summary + top terms")
    r = await do_adverse_events(access_context=ctx_json)
    print(f"  Status: {r['status']}")
    if r["status"] == "success":
        s = r["data"]["summary"]
        p("total_events",     s.get("total_events", 0))
        p("patients_with_ae", s.get("patients_with_ae", 0))
        p("serious",          s.get("serious_count", 0))
        p("severe",           s.get("severe_count", 0))
        top = r["data"]["top_event_terms"][:5]
        p("top_terms",        [t["ae_term"] for t in top])

    section("get_adverse_events — grouped by severity")
    r = await do_adverse_events(group_by="severity", access_context=ctx_json)
    if r["status"] == "success":
        for g in r["data"].get("grouped", []):
            print(f"    {g.get('severity','?')}: {g['count']}")

    section("get_adverse_events — severity filter (Severe)")
    r = await do_adverse_events(severity="Severe", access_context=ctx_json)
    if r["status"] == "success":
        p("severe_only_total", r["data"]["summary"].get("total_events", 0))

    section("get_adverse_events — aggregate enforcement (ceiling)")
    r = await do_adverse_events(access_context=agg_context(ctx_json))
    p("effective_level", r.get("metadata", {}).get("effective_access_level", r["status"]))


async def test_lab_results(ctx_json: str) -> None:
    section("get_lab_results — all tests summary")
    r = await do_lab_results(access_context=ctx_json)
    print(f"  Status: {r['status']}")
    if r["status"] == "success":
        stats = r["data"]["test_statistics"]
        p("unique_tests", len(stats))
        for s in stats[:4]:
            print(f"    {s['test_name']:25s} mean={s.get('mean_value')}"
                  f"  [{s.get('min_value')}, {s.get('max_value')}] {s.get('result_unit','')}")

    section("get_lab_results — filtered by test_name 'glucose'")
    r = await do_lab_results(test_name="glucose", access_context=ctx_json)
    if r["status"] == "success":
        for s in r["data"]["test_statistics"]:
            print(f"    {s['test_name']}  n={s['count']}  mean={s.get('mean_value')}")
    else:
        print(f"  {r.get('error', 'no results')}")


async def test_vital_signs(ctx_json: str) -> None:
    section("get_vital_signs — all types")
    r = await do_vital_signs(access_context=ctx_json)
    print(f"  Status: {r['status']}")
    if r["status"] == "success":
        for v in r["data"]["vital_statistics"][:6]:
            print(f"    {v['vital_type']:25s} mean={v.get('mean_value')} {v.get('result_unit','')}")

    section("get_vital_signs — type filter 'blood pressure'")
    r = await do_vital_signs(vital_type="blood pressure", access_context=ctx_json)
    if r["status"] == "success":
        for v in r["data"]["vital_statistics"]:
            print(f"    {v['vital_type']}: mean={v.get('mean_value')} (n={v.get('count')})")
    else:
        print(f"  {r.get('error', 'no results')}")


async def test_concomitant_meds(ctx_json: str) -> None:
    section("get_concomitant_medications — top medications")
    r = await do_concomitant_meds(access_context=ctx_json)
    print(f"  Status: {r['status']}")
    if r["status"] == "success":
        for m in r["data"]["medication_frequency"][:5]:
            print(f"    {m['medication_name']:30s} patients={m['patient_count']}")
    else:
        print(f"  {r.get('error','no data')}")


async def test_compare_arms(ctx_json: str, trial_id: str) -> None:
    section(f"compare_treatment_arms — trial {trial_id[:8]}…")
    r = await do_compare_arms(trial_id=trial_id, access_context=ctx_json)
    print(f"  Status: {r['status']}")
    if r["status"] == "success":
        print("  Demographics by arm:")
        for d in r["data"]["demographics_by_arm"]:
            print(f"    {str(d.get('arm_assigned','?'))[:35]:35s}"
                  f" n={d.get('patient_count',0)}"
                  f" age={d.get('avg_age')}"
                  f" M={d.get('male_count',0)}/F={d.get('female_count',0)}")
        print("  AE by arm:")
        for a in r["data"]["adverse_events_by_arm"]:
            print(f"    {str(a.get('arm_assigned','?'))[:35]:35s}"
                  f" events={a.get('total_events',0)}"
                  f" serious={a.get('serious_events',0)}")





async def test_search_documents(ctx_json: str) -> None:
    section("search_documents — 'adverse events nausea'")
    r = await do_search_documents(query="adverse events nausea", access_context=ctx_json)
    print(f"  Status: {r['status']}")
    if r["status"] == "success":
        p("total_chunks", r["data"]["total_found"])
        for c in r["data"]["chunks"][:3]:
            print(f"    [{c['nct_id']} / {c['section']}] score={c['score']}")
            print(f"      {c['text'][:100]}…")
    else:
        print(f"  {r.get('error')}")

    section("search_documents — 'eligibility criteria age' (section filter)")
    r = await do_search_documents(
        query="eligibility criteria age", section="eligibility", access_context=ctx_json
    )
    if r["status"] == "success":
        p("total_chunks", r["data"]["total_found"])
        for c in r["data"]["chunks"][:2]:
            print(f"    [{c['nct_id']} / {c['section']}] score={c['score']}")


async def test_authorization_enforcement(ctx_json: str, trial_ids: list[str]) -> None:
    section("Authorization: cohort filter reduces patient count")
    from tools.patient_analytics import _parse_trial_ids, VALID_GROUP_BY

    filtered_tid = trial_ids[0]
    ctx = AccessContext.from_json(ctx_json)

    params, idx = [], 1
    aw, ap, idx = ctx.build_authorized_patient_filter([filtered_tid], idx)
    params.extend(ap)
    sql = f"""
        SELECT COUNT(DISTINCT p.patient_id) AS n
        FROM patient p
        JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
        WHERE {aw}
    """
    filtered_count = (await postgres.fetch(sql, *params))[0]["n"]

    ctx2 = json.loads(ctx_json)
    ctx2["patient_filters"] = {}
    ctx2_obj = AccessContext.from_json(json.dumps(ctx2))
    params2, idx2 = [], 1
    aw2, ap2, idx2 = ctx2_obj.build_authorized_patient_filter([filtered_tid], idx2)
    params2.extend(ap2)
    sql2 = sql.replace(aw, aw2)
    unfiltered_count = (await postgres.fetch(sql2, *params2))[0]["n"]

    p("filtered_count (Hispanic 10-100)",  filtered_count)
    p("unfiltered_count",                   unfiltered_count)
    if filtered_count <= unfiltered_count:
        print("  ✓ Cohort filter enforced correctly")
    else:
        print("  ✗ ERROR: filtered > unfiltered!")

    section("Authorization: aggregate-only blocks individual records in AE query")
    r = await do_adverse_events(access_context=agg_context(ctx_json))
    level = r.get("metadata", {}).get("effective_access_level", "unknown")
    p("effective_access_level", level)
    if level == "aggregate":
        print("  ✓ Ceiling principle enforced")
    else:
        print("  ✗ ERROR: expected aggregate")

    section("Authorization: unauthorized trial → ACCESS_DENIED")
    ctx3 = json.loads(ctx_json)
    ctx3["allowed_trial_ids"] = []
    ctx3["access_levels"] = {}
    ctx3["patient_filters"] = {}
    r = await do_adverse_events(access_context=json.dumps(ctx3))
    p("status", r["status"])
    if r["status"] == "error":
        print("  ✓ ACCESS_DENIED returned correctly")


async def inspect_neo4j_schema() -> None:
    """Print actual Neo4j schema for debugging."""
    section("Neo4j Schema Inspection")

    try:
        rows = await neo4j_client.run_cypher(
            "CALL db.relationshipTypes() YIELD relationshipType "
            "RETURN relationshipType ORDER BY relationshipType"
        )
        print("  Relationship types:")
        for r in rows:
            print(f"    - {r['relationshipType']}")
    except Exception as e:
        print(f"  relationshipTypes error: {e}")

    try:
        rows = await neo4j_client.run_cypher(
            "MATCH (a)-[r]->(b) "
            "RETURN DISTINCT labels(a) AS from_l, type(r) AS rel, labels(b) AS to_l "
            "ORDER BY rel"
        )
        print("  Relationship patterns:")
        for r in rows:
            print(f"    {r['from_l']} -[{r['rel']}]-> {r['to_l']}")
    except Exception as e:
        print(f"  patterns error: {e}")

    try:
        rows = await neo4j_client.run_cypher(
            "MATCH (n) "
            "RETURN DISTINCT labels(n) AS labels, keys(n) AS props "
            "LIMIT 10"
        )
        print("  Node properties:")
        for r in rows:
            print(f"    {r['labels']}: {r['props']}")
    except Exception as e:
        print(f"  properties error: {e}")


async def do_drug_relationships(
    drug_name="", condition_name="", ae_term="", access_context=""
) -> dict:
    """
    Query Neo4j using the actual schema:
      (ClinicalTrial)-[:TESTS_INTERVENTION]->(Drug)
      (ClinicalTrial)-[:STUDIES]->(Condition)
      (Patient)-[:ENROLLED_IN]->(ClinicalTrial)
      (Patient)-[:HAS_CONDITION]->(Condition)
      (Patient)-[:EXPERIENCED]->(AdverseEvent)
      (Condition)-[:COMORBID_WITH]->(Condition)
    """
    ctx = AccessContext.from_json(access_context)
    if not ctx.allowed_trial_ids:
        return {"status": "error", "error": "No access"}

    trial_ids_cypher = (
        "[" + ", ".join(f'"{t}"' for t in ctx.allowed_trial_ids) + "]"
    )
    results: dict = {
        "drugs_in_trials":      [],
        "conditions_in_trials": [],
        "drug_condition_links": [],
        "adverse_events":       [],
        "comorbid_conditions":  [],
    }

    if drug_name.strip():
        # Trials testing this drug
        try:
            rows = await neo4j_client.run_cypher(
                f"""
                MATCH (t:ClinicalTrial)-[:TESTS_INTERVENTION]->(d:Drug)
                WHERE t.trial_id IN {trial_ids_cypher}
                  AND (toLower(d.name) CONTAINS toLower($dn)
                       OR toLower(d.generic_name) CONTAINS toLower($dn))
                RETURN DISTINCT
                    d.name AS drug_name, d.generic_name AS generic_name,
                    d.type AS drug_type, t.nct_id AS nct_id,
                    t.phase AS phase, t.therapeutic_area AS therapeutic_area
                ORDER BY t.nct_id LIMIT 30
                """,
                {"dn": drug_name.strip()},
            )
            results["drugs_in_trials"] = rows
        except Exception as e:
            print(f"  [warn] drug→trial query: {e}")

        # Conditions studied in same trials
        try:
            rows = await neo4j_client.run_cypher(
                f"""
                MATCH (t:ClinicalTrial)-[:TESTS_INTERVENTION]->(d:Drug)
                MATCH (t)-[:STUDIES]->(c:Condition)
                WHERE t.trial_id IN {trial_ids_cypher}
                  AND (toLower(d.name) CONTAINS toLower($dn)
                       OR toLower(d.generic_name) CONTAINS toLower($dn))
                RETURN DISTINCT
                    d.name AS drug_name, c.name AS condition_name,
                    c.icd10_code AS icd10_code, t.nct_id AS nct_id
                ORDER BY c.name LIMIT 30
                """,
                {"dn": drug_name.strip()},
            )
            results["drug_condition_links"] = rows
        except Exception as e:
            print(f"  [warn] drug→condition query: {e}")

    if condition_name.strip():
        # Authorized trials studying this condition
        try:
            rows = await neo4j_client.run_cypher(
                f"""
                MATCH (t:ClinicalTrial)-[:STUDIES]->(c:Condition)
                WHERE t.trial_id IN {trial_ids_cypher}
                  AND toLower(c.name) CONTAINS toLower($cn)
                RETURN DISTINCT
                    c.name AS condition_name, c.icd10_code AS icd10_code,
                    t.nct_id AS nct_id, t.phase AS phase,
                    t.therapeutic_area AS therapeutic_area
                ORDER BY t.nct_id LIMIT 30
                """,
                {"cn": condition_name.strip()},
            )
            results["conditions_in_trials"] = rows
        except Exception as e:
            print(f"  [warn] condition→trial query: {e}")

        # Comorbid conditions
        try:
            rows = await neo4j_client.run_cypher(
                """
                MATCH (c1:Condition)-[:COMORBID_WITH]->(c2:Condition)
                WHERE toLower(c1.name) CONTAINS toLower($cn)
                   OR toLower(c2.name) CONTAINS toLower($cn)
                RETURN DISTINCT
                    c1.name AS condition_a, c2.name AS condition_b,
                    c1.icd10_code AS icd10_a, c2.icd10_code AS icd10_b
                LIMIT 20
                """,
                {"cn": condition_name.strip()},
            )
            results["comorbid_conditions"] = rows
        except Exception as e:
            print(f"  [warn] comorbid query: {e}")

    if ae_term.strip():
        # Adverse events in authorized trials
        try:
            rows = await neo4j_client.run_cypher(
                f"""
                MATCH (p:Patient)-[:EXPERIENCED]->(ae:AdverseEvent)
                MATCH (p)-[:ENROLLED_IN]->(t:ClinicalTrial)
                WHERE t.trial_id IN {trial_ids_cypher}
                  AND (toLower(ae.term) CONTAINS toLower($ae)
                       OR toLower(ae.meddra_pt) CONTAINS toLower($ae))
                RETURN DISTINCT
                    ae.term AS ae_term, ae.meddra_pt AS meddra_pt,
                    ae.soc AS soc, t.nct_id AS nct_id,
                    COUNT(p) AS patient_count
                ORDER BY patient_count DESC LIMIT 30
                """,
                {"ae": ae_term.strip()},
            )
            results["adverse_events"] = rows
        except Exception as e:
            print(f"  [warn] AE query: {e}")

    return {"status": "success", "data": results}


async def do_vital_signs(trial_ids="", vital_type="", access_context="") -> dict:
    ctx = AccessContext.from_json(access_context)
    requested = [t.strip() for t in trial_ids.split(",") if t.strip()] if trial_ids else []
    authorized = ctx.validate_trial_access(requested)
    if not authorized:
        return {"status": "error", "error": "No access"}

    params, idx = [], 1
    aw, ap, idx = ctx.build_authorized_patient_filter(authorized, idx)
    params.extend(ap)
    extra = []
    if vital_type.strip():
        extra.append(f"LOWER(vs.test_name) LIKE LOWER(${idx})")
        params.append(f"%{vital_type.strip()}%")
        idx += 1
    where = " AND ".join([f"({aw})"] + extra)

    # Use actual column names: test_name, result_value, result_unit
    sql = f"""
        SELECT
            vs.test_name                            AS vital_type,
            vs.result_unit                          AS unit,
            COUNT(*)                                AS count,
            ROUND(AVG(vs.result_value)::numeric, 2) AS mean_value,
            MIN(vs.result_value)                    AS min_value,
            MAX(vs.result_value)                    AS max_value
        FROM vital_sign vs
        JOIN patient p ON vs.patient_id = p.patient_id
        JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
        WHERE vs.trial_id = pte.trial_id AND {where}
        GROUP BY vs.test_name, vs.result_unit
        ORDER BY count DESC
    """
    rows = await postgres.fetch(sql, *params)
    return {
        "status": "success",
        "data": {"vital_statistics": [serialize_row(r) for r in rows]},
    }


async def do_compare_arms(trial_id="", access_context="") -> dict:
    ctx = AccessContext.from_json(access_context)
    if not ctx.has_access(trial_id):
        return {"status": "error", "error": "No access"}

    params, idx = [], 1
    aw, ap, idx = ctx.build_authorized_patient_filter([trial_id], idx)
    params.extend(ap)

    demo_sql = f"""
        SELECT p.arm_assigned,
               COUNT(DISTINCT p.patient_id) AS patient_count,
               ROUND(AVG(p.age)::numeric, 1) AS avg_age,
               COUNT(*) FILTER (WHERE p.sex = 'M') AS male_count,
               COUNT(*) FILTER (WHERE p.sex = 'F') AS female_count
        FROM patient p
        JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
        WHERE {aw} GROUP BY p.arm_assigned
    """
    # ae_term not event_term
    ae_sql = f"""
        SELECT p.arm_assigned,
               COUNT(*) AS total_events,
               COUNT(*) FILTER (WHERE ae.serious = TRUE) AS serious_events,
               COUNT(DISTINCT ae.ae_term) AS unique_ae_terms
        FROM adverse_event ae
        JOIN patient p ON ae.patient_id = p.patient_id
        JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
        WHERE ae.trial_id = pte.trial_id AND {aw}
        GROUP BY p.arm_assigned
    """
    demo_rows = await postgres.fetch(demo_sql, *params)
    ae_rows   = await postgres.fetch(ae_sql, *params)

    return {
        "status": "success",
        "data": {
            "demographics_by_arm":     [serialize_row(r) for r in demo_rows],
            "adverse_events_by_arm":   [serialize_row(r) for r in ae_rows],
        },
    }


async def test_drug_relationships(ctx_json: str, nct_ids: list[str]) -> None:
    # Sample drug from intervention table using correct column name
    drug_rows = await postgres.fetch(
        """
        SELECT DISTINCT i.name AS drug_name
        FROM intervention i
        JOIN trial_arm ta ON i.arm_id = ta.arm_id
        JOIN clinical_trial ct ON ta.trial_id = ct.trial_id
        WHERE ct.nct_id = ANY($1::text[])
          AND i.name IS NOT NULL
        LIMIT 5
        """,
        nct_ids,
    )
    sample_drug = drug_rows[0]["drug_name"] if drug_rows else "Nivolumab"

    section(f"find_drug_condition_relationships — drug: '{sample_drug}'")
    r = await do_drug_relationships(drug_name=sample_drug, access_context=ctx_json)
    print(f"  Status: {r['status']}")
    if r["status"] == "success":
        trials = r["data"].get("drugs_in_trials", [])
        links  = r["data"].get("drug_condition_links", [])
        p("trials_testing_drug",  len(trials))
        for t in trials[:4]:
            print(f"    {t.get('drug_name')} in {t.get('nct_id')} "
                  f"({t.get('phase')}) [{t.get('therapeutic_area')}]")
        p("drug_condition_links", len(links))
        for lnk in links[:4]:
            print(f"    {lnk.get('drug_name')} ↔ {lnk.get('condition_name')} "
                  f"[{lnk.get('icd10_code')}] in {lnk.get('nct_id')}")
    else:
        print(f"  Error: {r.get('error')}")

    # Condition from Neo4j
    cond_rows = await neo4j_client.run_cypher(
        "MATCH (c:Condition) RETURN c.name AS name LIMIT 5"
    )
    sample_condition = cond_rows[0]["name"] if cond_rows else "diabetes"

    section(f"find_drug_condition_relationships — condition: '{sample_condition}'")
    r = await do_drug_relationships(
        condition_name=sample_condition, access_context=ctx_json
    )
    if r["status"] == "success":
        conds    = r["data"].get("conditions_in_trials", [])
        comorbid = r["data"].get("comorbid_conditions", [])
        p("conditions_in_authorized_trials", len(conds))
        for c in conds[:4]:
            print(f"    {c.get('condition_name')} [{c.get('icd10_code')}] "
                  f"in {c.get('nct_id')} ({c.get('phase')})")
        p("comorbid_conditions", len(comorbid))
        for c in comorbid[:3]:
            print(f"    {c.get('condition_a')} ↔ {c.get('condition_b')}")

    # AE term from Neo4j
    ae_rows = await neo4j_client.run_cypher(
        "MATCH (ae:AdverseEvent) RETURN ae.term AS term LIMIT 5"
    )
    sample_ae = ae_rows[0]["term"] if ae_rows else "nausea"

    section(f"find_drug_condition_relationships — ae_term: '{sample_ae}'")
    r = await do_drug_relationships(ae_term=sample_ae, access_context=ctx_json)
    if r["status"] == "success":
        aes = r["data"].get("adverse_events", [])
        p("ae_results", len(aes))
        for ae in aes[:4]:
            print(f"    {ae.get('ae_term')} [{ae.get('meddra_pt')}] "
                  f"in {ae.get('nct_id')}: {ae.get('patient_count')} patients")
# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    print(f"\n{SEP}\nPhase 1 — Complete MCP Tool Tests\n{SEP}")

    await postgres.init_pool()
    await qdrant_client.init_client()
    await neo4j_client.init_driver()

    try:
        from server import mcp
        tm = getattr(mcp, "_tool_manager", None)
        if tm:
            tools = sorted(getattr(tm, "_tools", {}).keys())
            print(f"\nRegistered MCP tools ({len(tools)}):")
            for name in tools:
                print(f"  - {name}")

        # Always inspect Neo4j schema first
        await inspect_neo4j_schema()

        ctx_json, trial_ids, nct_ids = await build_context()

        await test_search_trials(ctx_json)
        await test_adverse_events(ctx_json)
        await test_lab_results(ctx_json)
        await test_vital_signs(ctx_json)
        await test_concomitant_meds(ctx_json)
        await test_compare_arms(ctx_json, trial_ids[0])
        await test_drug_relationships(ctx_json, nct_ids)      # ← pass nct_ids
        await test_search_documents(ctx_json)
        await test_authorization_enforcement(ctx_json, trial_ids)

        print(f"\n{SEP}\nALL TESTS COMPLETE\n{SEP}\n")

    finally:
        await postgres.close_pool()
        await qdrant_client.close_client()
        await neo4j_client.close_driver()

if __name__ == "__main__":
    asyncio.run(main())