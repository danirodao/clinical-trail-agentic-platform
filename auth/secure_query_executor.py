"""
Secure Query Executor — Enforces aggregate-only access for researchers
without fine-grained assignments. Validates SQL before execution.
"""

import re
import logging
from typing import Optional

from auth.authorization_service import AccessProfile

logger = logging.getLogger(__name__)

# Patterns that indicate individual-level access attempts
INDIVIDUAL_ACCESS_PATTERNS = [
    r"\bpatient_id\b",
    r"\bsubject_id\b",
    r"\bpatient\.age\b",
    r"\bpatient\.sex\b",
    r"\bpatient\.race\b",
    r"\bpatient\.ethnicity\b",
    r"\bdate_of_birth\b",
    r"\bLIMIT\s+1\b",
    r"\bFETCH\s+FIRST\s+1\b",
    r"\bpatient_medication\.medication_name\b",  # Individual med history
    r"\badverse_event\.ae_term\b",               # Individual AE
]

# Patterns that are required for aggregate queries
AGGREGATE_REQUIRED_PATTERNS = [
    r"\bCOUNT\s*\(",
    r"\bAVG\s*\(",
    r"\bSUM\s*\(",
    r"\bMIN\s*\(",
    r"\bMAX\s*\(",
    r"\bSTDDEV\s*\(",
    r"\bVARIANCE\s*\(",
    r"\bGROUP\s+BY\b",
    r"\bHAVING\b",
]


class AggregateViolation(Exception):
    """Raised when an aggregate-only user attempts individual-level access."""
    pass


class SecureQueryExecutor:
    """
    Validates and executes SQL queries with authorization enforcement.
    """

    @staticmethod
    def validate_aggregate_only_sql(sql: str) -> tuple[bool, Optional[str]]:
        """
        Validate that a SQL query only produces aggregate results.
        Returns (is_valid, error_message).
        """
        sql_upper = sql.upper().strip()

        # Check for forbidden individual-access patterns
        for pattern in INDIVIDUAL_ACCESS_PATTERNS:
            if re.search(pattern, sql, re.IGNORECASE):
                return False, (
                    f"Query references individual-level field matching '{pattern}'. "
                    f"You only have aggregate access. Use COUNT(), AVG(), etc."
                )

        # Ensure at least one aggregate function is present
        has_aggregate = any(
            re.search(p, sql, re.IGNORECASE)
            for p in AGGREGATE_REQUIRED_PATTERNS
        )
        if not has_aggregate:
            return False, (
                "Aggregate-only access requires at least one aggregate function "
                "(COUNT, AVG, SUM, MIN, MAX) or GROUP BY clause."
            )

        return True, None

    @staticmethod
    def inject_trial_filter(sql: str, profile: AccessProfile) -> str:
        """
        Inject trial_id filter into a SQL query based on access profile.
        Ensures the user can only see data from authorized trials.
        """
        if profile.role == "domain_owner":
            return sql  # No restriction

        trial_filter = profile.sql_trial_filter

        # Smart injection: find WHERE clause or add one
        if re.search(r"\bWHERE\b", sql, re.IGNORECASE):
            # Append to existing WHERE
            sql = re.sub(
                r"\bWHERE\b",
                f"WHERE ({trial_filter}) AND",
                sql,
                count=1,
                flags=re.IGNORECASE,
            )
        elif re.search(r"\bFROM\b", sql, re.IGNORECASE):
            # Add WHERE after FROM ... (before GROUP BY / ORDER BY / LIMIT)
            insertion_point = re.search(
                r"(?=\b(?:GROUP|ORDER|HAVING|LIMIT|$))", sql, re.IGNORECASE
            )
            if insertion_point:
                pos = insertion_point.start()
                sql = sql[:pos] + f" WHERE {trial_filter} " + sql[pos:]
            else:
                sql += f" WHERE {trial_filter}"

        return sql

    @staticmethod
    def validate_k_anonymity(result_rows: list[dict], group_column: str, min_k: int = 5) -> list[dict]:
        """
        Post-filter: Remove groups with fewer than min_k members (k-anonymity).
        Assumes result_rows have a 'count' column from GROUP BY aggregation.
        """
        filtered = []
        suppressed = 0

        for row in result_rows:
            count_val = row.get("count", row.get("cnt", 0))
            if count_val >= min_k:
                filtered.append(row)
            else:
                suppressed += 1

        if suppressed > 0:
            logger.info(
                f"K-anonymity: Suppressed {suppressed} groups with < {min_k} members"
            )

        return filtered