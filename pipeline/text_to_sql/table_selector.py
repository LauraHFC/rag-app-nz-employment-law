"""
table_selector.py
=================
Haiku-powered dynamic table selector for the NZ Labour Market Text-to-SQL pipeline.

Given a user query, asks Claude Haiku to identify which of the 11 DuckDB tables
are relevant. Returns a list of 1–5 table names.

Why Haiku (not Sonnet)?
  - Table selection is a lightweight classification task — 11 options, each with a
    short description. Haiku is faster and cheaper; Sonnet is reserved for SQL generation.

Public API:
    select_tables(query: str, api_key: str | None = None) -> TableSelectionResult

    TableSelectionResult:
        .tables       : list[str]  — selected table names (1–5)
        .reasoning    : str        — Haiku's explanation (for debugging/logging)
        .raw_response : str        — raw LLM output (for debugging)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import anthropic

from pipeline.text_to_sql.schema_context import TABLE_METADATA

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HAIKU_MODEL = "claude-haiku-4-5-20251001"
MAX_TABLES = 5
MIN_TABLES = 1

# Compact table descriptions for the selector prompt (shorter than full schema context)
_TABLE_DESCRIPTIONS: dict[str, str] = {
    name: meta["description"].split(".")[0]  # first sentence only
    for name, meta in TABLE_METADATA.items()
}

_SELECTOR_SYSTEM_PROMPT = """\
You are a database table selector for a New Zealand labour market analytics system.
You will be given a user question and a list of available database tables with short descriptions.
Your job is to identify which tables are needed to answer the question.

Rules:
- Select between 1 and 5 tables. Select the minimum needed to answer the question.
- If the question is about national aggregates (no breakdown), prefer tables without dimension columns.
- Never select `earnings_by_qualification` together with other tables for a JOIN on industry — it uses incompatible industry groupings.
- For gender pay gap questions, select `avg_hourly_earnings` (it has a `sex` column).
- For regional questions, select `underutilisation` (the only table with a `region` column).
- For full-time/part-time questions, select `employed_ft_pt_status`.
- For qualification/education wage questions, select `earnings_by_qualification`.

Respond ONLY with a JSON object in this exact format:
{
  "tables": ["table_name_1", "table_name_2"],
  "reasoning": "Brief explanation of why each table is needed."
}
"""


def _build_selector_user_prompt(query: str) -> str:
    """Build the user message for Haiku: query + compact table list."""
    table_list = "\n".join(
        f"- `{name}`: {desc}"
        for name, desc in _TABLE_DESCRIPTIONS.items()
    )
    return f"""User question: {query}

Available tables:
{table_list}

Select the relevant tables and respond with JSON only."""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class TableSelectionResult:
    tables: list[str]
    reasoning: str
    raw_response: str
    query: str
    model: str = HAIKU_MODEL

    def __repr__(self) -> str:
        return f"TableSelectionResult(tables={self.tables}, query={self.query!r})"


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def select_tables(
    query: str,
    api_key: str | None = None,
    max_tables: int = MAX_TABLES,
) -> TableSelectionResult:
    """
    Use Claude Haiku to select relevant tables for the given user query.

    Args:
        query:      Natural-language user question.
        api_key:    Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.
        max_tables: Maximum tables to accept (default 5). Trims if Haiku returns more.

    Returns:
        TableSelectionResult with .tables (list[str]) and .reasoning (str).

    Raises:
        ValueError: If the API key is missing or the response cannot be parsed.
        anthropic.APIError: On API-level errors.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError(
            "ANTHROPIC_API_KEY not set. Pass api_key= or set the environment variable."
        )

    client = anthropic.Anthropic(api_key=key)

    message = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=512,
        system=_SELECTOR_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": _build_selector_user_prompt(query)}
        ],
    )

    raw = message.content[0].text.strip()

    # Parse JSON — strip markdown fences if present
    json_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Haiku returned non-JSON response. Raw output:\n{raw}\nError: {e}"
        ) from e

    # Validate table names against known tables
    selected = parsed.get("tables", [])
    valid = [t for t in selected if t in TABLE_METADATA]
    invalid = [t for t in selected if t not in TABLE_METADATA]

    if not valid:
        raise ValueError(
            f"Haiku selected no valid tables. Got: {selected}. Known tables: {list(TABLE_METADATA.keys())}"
        )

    # Trim to max_tables
    if len(valid) > max_tables:
        valid = valid[:max_tables]

    return TableSelectionResult(
        tables=valid,
        reasoning=parsed.get("reasoning", ""),
        raw_response=raw,
        query=query,
    )


# ---------------------------------------------------------------------------
# Build a focused schema prompt for only the selected tables
# ---------------------------------------------------------------------------

def get_focused_schema_prompt(tables: list[str]) -> str:
    """
    Return a schema context prompt restricted to the selected tables only.
    Used by the SQL generator to keep the prompt focused and token-efficient.
    """
    from pipeline.text_to_sql.schema_context import get_schema_context_prompt, TABLE_METADATA as META

    # Re-use the full prompt builder but filter to selected tables only
    # We do this by temporarily patching TABLE_METADATA (non-destructive copy)
    filtered_meta = {k: v for k, v in META.items() if k in tables}

    lines: list[str] = []
    lines.append("# Selected Tables — Schema Reference")
    lines.append("")
    lines.append(
        "Database: `data/stats_nz.db` (DuckDB, read-only)  "
        "Period format: `YYYY.QN` (e.g. `2025.Q4`)  "
        "Columns in every table: `period`, [dimension cols], `metric`, `value`, `source_file`"
    )
    lines.append("")
    lines.append("## ⚠️ Critical JOIN Rules")
    lines.append(
        "1. Never JOIN on `industry` across tables — naming is inconsistent. JOIN key: **`period` only**.  \n"
        "2. `earnings_by_qualification` is annual (`.Q2` periods only).  \n"
        "3. `gross_earnings` and `filled_jobs_hours` share industry names — safe to JOIN on period + industry."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    for tname, meta in filtered_meta.items():
        lines.append(f"## `{tname}`")
        lines.append(f"**{meta['description']}**")
        lines.append("")
        lines.append(
            f"Source: {meta['source']} | Rows: {meta['rows']:,} | "
            f"Periods: {meta['period_range'][0]} → {meta['period_range'][1]} ({meta['period_count']} quarters)"
        )
        lines.append("")
        lines.append("| Column | Values |")
        lines.append("|--------|--------|")
        lines.append(f"| `period` | `{meta['period_range'][0]}` … `{meta['period_range'][1]}` |")
        for dim, vals in meta["dimensions"].items():
            v_str = ", ".join(f"`{v}`" for v in vals[:8])
            if len(vals) > 8:
                v_str += f" … (+{len(vals)-8} more)"
            lines.append(f"| `{dim}` | {v_str} |")
        metrics_str = ", ".join(f"`{m}`" for m in meta["metrics"])
        lines.append(f"| `metric` | {metrics_str} |")
        lines.append(f"| `value` | numeric |")
        lines.append("")
        if meta.get("notes"):
            lines.append(f"> **Notes:** {meta['notes']}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    test_queries = [
        "What is the current unemployment rate in New Zealand?",
        "Which industry has the largest gender pay gap?",
        "How does the youth unemployment rate compare to the national average?",
        "What is the underutilisation rate in Auckland?",
        "How do postgraduate earners compare to those with no qualification?",
    ]

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("Set ANTHROPIC_API_KEY to run smoke test.")
        sys.exit(0)

    for q in test_queries:
        result = select_tables(q)
        print(f"Q: {q}")
        print(f"   → {result.tables}")
        print(f"   Reasoning: {result.reasoning[:80]}...")
        print()
