"""
sql_generator.py
================
Sonnet-powered SQL generator for the NZ Labour Market Text-to-SQL pipeline.

Given:
  - A user query
  - A list of selected tables (from table_selector.py)
  - A focused schema prompt (from table_selector.get_focused_schema_prompt)

Returns a SQLGenerationResult with the SQL string and metadata.

Includes a retry loop (max 2 retries) that feeds execution errors back to Sonnet
for self-correction.

Public API:
    generate_sql(
        query: str,
        selected_tables: list[str],
        focused_schema: str,
        api_key: str | None = None,
        max_retries: int = 2,
    ) -> SQLGenerationResult

    SQLGenerationResult:
        .sql            : str         — the final SQL string
        .tables_used    : list[str]   — tables referenced in the SQL
        .attempts       : int         — how many attempts were needed (1 = first try)
        .error_history  : list[str]   — errors from previous failed attempts
        .raw_response   : str         — last raw LLM response
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import anthropic

from pipeline.text_to_sql.few_shot_examples import get_few_shot_prompt_block

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SONNET_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 2048

_SQL_SYSTEM_PROMPT = """\
You are an expert DuckDB SQL writer for a New Zealand labour market analytics database.

Your job is to write a single, correct, read-only SELECT statement that answers the user's question.

Rules:
1. Output ONLY the SQL query — no explanation, no markdown, no commentary. Just the raw SQL.
2. Use only the tables and columns described in the schema provided.
3. Always use exact string values for dimension filters (copy from schema — case-sensitive).
4. For "latest" or "most recent" data, use: WHERE period = (SELECT MAX(period) FROM <table>)
5. Period format is 'YYYY.QN' (e.g. '2025.Q4'). String comparison works for ordering/filtering.
6. NEVER join tables on `industry` — industry naming is inconsistent across tables. JOIN on `period` only.
7. `earnings_by_qualification` is annual (.Q2 periods). To join with quarterly tables, filter with: period LIKE '%.Q2'
8. `gross_earnings` and `filled_jobs_hours` share the same industry names — safe to join on period AND industry.
9. Use CTEs (WITH clauses) for multi-table queries — easier to read and debug.
10. Exclude aggregate rows (e.g. 'Total All Industries', 'Total All Ages') unless the question asks for totals.
11. Use ROUND(..., 2) for computed ratios and percentages.
12. Use NULLIF(denominator, 0) to avoid division by zero.
13. The database is read-only. Never generate INSERT, UPDATE, DELETE, DROP, or any write statement.
"""


def _build_generation_prompt(
    query: str,
    focused_schema: str,
    few_shot_block: str,
    error_history: list[str],
) -> str:
    """Build the user message for Sonnet."""
    parts = [focused_schema, "", few_shot_block, "---", ""]

    if error_history:
        parts.append("## Previous Attempt Errors")
        for i, err in enumerate(error_history, 1):
            parts.append(f"Attempt {i} failed with: {err}")
        parts.append("Please correct the SQL to avoid these errors.")
        parts.append("")

    parts.append(f"## User Question")
    parts.append(query)
    parts.append("")
    parts.append("Write the SQL query now (SQL only, no explanation):")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# SQL extraction & cleaning
# ---------------------------------------------------------------------------

def _extract_sql(raw: str) -> str:
    """
    Extract SQL from the model response.
    Handles cases where Sonnet wraps it in ```sql ... ``` fences despite instructions.
    """
    # Strip markdown code fences
    fenced = re.search(r"```(?:sql)?\s*([\s\S]+?)\s*```", raw, re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    # Otherwise return the whole response stripped
    return raw.strip()


_WRITE_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|REPLACE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)

def _assert_read_only(sql: str) -> None:
    """Raise ValueError if SQL contains write operations."""
    match = _WRITE_PATTERN.search(sql)
    if match:
        raise ValueError(f"Generated SQL contains forbidden write operation: {match.group()}")
    if not re.search(r"\bSELECT\b", sql, re.IGNORECASE):
        raise ValueError("Generated SQL does not contain a SELECT statement.")


def _extract_table_references(sql: str, known_tables: list[str]) -> list[str]:
    """Return which known table names appear in the SQL (case-insensitive)."""
    sql_lower = sql.lower()
    return [t for t in known_tables if t.lower() in sql_lower]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class SQLGenerationResult:
    sql: str
    tables_used: list[str]
    attempts: int
    error_history: list[str] = field(default_factory=list)
    raw_response: str = ""
    query: str = ""
    model: str = SONNET_MODEL

    def __repr__(self) -> str:
        return (
            f"SQLGenerationResult(attempts={self.attempts}, "
            f"tables_used={self.tables_used}, sql_len={len(self.sql)})"
        )


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def generate_sql(
    query: str,
    selected_tables: list[str],
    focused_schema: str,
    api_key: str | None = None,
    max_retries: int = 2,
) -> SQLGenerationResult:
    """
    Use Claude Sonnet to generate a DuckDB SQL query for the user question.

    Args:
        query:           Natural-language user question.
        selected_tables: Table names chosen by the table selector.
        focused_schema:  Schema context prompt (from get_focused_schema_prompt()).
        api_key:         Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.
        max_retries:     How many times to retry on SQL error (default 2).

    Returns:
        SQLGenerationResult with .sql, .tables_used, .attempts, .error_history.

    Raises:
        ValueError: If SQL cannot be generated after all retries.
        anthropic.APIError: On API-level errors.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set.")

    client = anthropic.Anthropic(api_key=key)
    few_shot_block = get_few_shot_prompt_block()

    error_history: list[str] = []
    last_raw = ""
    last_sql = ""

    for attempt in range(1, max_retries + 2):  # attempts: 1, 2, 3 (with max_retries=2)
        user_prompt = _build_generation_prompt(
            query=query,
            focused_schema=focused_schema,
            few_shot_block=few_shot_block,
            error_history=error_history,
        )

        message = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=MAX_TOKENS,
            system=_SQL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        last_raw = message.content[0].text.strip()
        last_sql = _extract_sql(last_raw)

        # Guard: read-only check
        try:
            _assert_read_only(last_sql)
        except ValueError as e:
            error_history.append(str(e))
            if attempt > max_retries:
                break
            continue

        # Success — SQL passed read-only guard
        tables_used = _extract_table_references(last_sql, selected_tables)
        return SQLGenerationResult(
            sql=last_sql,
            tables_used=tables_used,
            attempts=attempt,
            error_history=error_history,
            raw_response=last_raw,
            query=query,
        )

    raise ValueError(
        f"SQL generation failed after {max_retries + 1} attempts. "
        f"Last SQL:\n{last_sql}\nErrors: {error_history}"
    )


# ---------------------------------------------------------------------------
# Retry helper — called by query_pipeline after execution failure
# ---------------------------------------------------------------------------

def regenerate_sql_with_error(
    query: str,
    selected_tables: list[str],
    focused_schema: str,
    previous_sql: str,
    execution_error: str,
    api_key: str | None = None,
) -> SQLGenerationResult:
    """
    Ask Sonnet to fix a SQL that failed during execution.
    Called by query_pipeline when db_engine.query() raises an exception.

    Wraps generate_sql with a pre-populated error_history so Sonnet sees
    what went wrong on the first try.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set.")

    client = anthropic.Anthropic(api_key=key)
    few_shot_block = get_few_shot_prompt_block()

    error_context = [f"SQL:\n{previous_sql}\nError: {execution_error}"]
    user_prompt = _build_generation_prompt(
        query=query,
        focused_schema=focused_schema,
        few_shot_block=few_shot_block,
        error_history=error_context,
    )

    message = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=MAX_TOKENS,
        system=_SQL_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = message.content[0].text.strip()
    sql = _extract_sql(raw)
    _assert_read_only(sql)
    tables_used = _extract_table_references(sql, selected_tables)

    return SQLGenerationResult(
        sql=sql,
        tables_used=tables_used,
        attempts=2,
        error_history=error_context,
        raw_response=raw,
        query=query,
    )


# ---------------------------------------------------------------------------
# Smoke test (no API call — just structure check)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("sql_generator.py — structure check")
    print(f"  Model: {SONNET_MODEL}")
    print(f"  Max tokens: {MAX_TOKENS}")
    print(f"  _extract_sql test:")
    sample = "```sql\nSELECT * FROM labour_force_status LIMIT 5\n```"
    print(f"    Input:  {sample!r}")
    print(f"    Output: {_extract_sql(sample)!r}")
    print()
    print("  _assert_read_only test:")
    try:
        _assert_read_only("DROP TABLE foo")
        print("    ❌ Should have raised ValueError")
    except ValueError as e:
        print(f"    ✅ Correctly blocked: {e}")
    try:
        _assert_read_only("SELECT period, value FROM labour_force_status LIMIT 5")
        print("    ✅ Valid SELECT passed")
    except ValueError as e:
        print(f"    ❌ Wrongly rejected: {e}")
