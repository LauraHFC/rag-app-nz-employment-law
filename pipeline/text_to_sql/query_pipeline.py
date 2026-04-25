"""
query_pipeline.py
=================
Orchestrator for the NZ Labour Market Text-to-SQL pipeline.

Flow:
    user query
        → TableSelector (Haiku)   — picks 1–5 relevant tables
        → SQLGenerator (Sonnet)   — generates DuckDB SQL
        → DBEngine.query()        — executes SQL, returns pd.DataFrame
        → (retry if error)        — up to MAX_EXEC_RETRIES on execution failure
        → QueryResult             — structured output

Public API:
    run_query(query: str, api_key: str | None = None) -> QueryResult

    QueryResult:
        .query               : str              — original user question
        .sql                 : str              — final SQL executed
        .result              : pd.DataFrame     — query result (may be empty on failure)
        .tables_used         : list[str]        — tables referenced in SQL
        .selected_tables     : list[str]        — tables chosen by Haiku
        .selector_reasoning  : str             — Haiku's table selection rationale
        .sql_attempts        : int              — number of SQL generation attempts
        .success             : bool             — True if result is non-empty
        .error               : str | None       — last error if pipeline failed
        .out_of_range_warning: str | None       — set when query references years before 2015.Q1;
                                                   Phase 3 answer synthesis should surface this to the user
        .chart               : dict | None      — ChartConfig for frontend visualisation, or None

Usage:
    from pipeline.text_to_sql.query_pipeline import run_query

    result = run_query("What is the current unemployment rate in New Zealand?")
    if result.success:
        print(result.sql)
        print(result.result)
    else:
        print(f"Failed: {result.error}")
"""

from __future__ import annotations

import os
import re
import traceback
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from pipeline.db_engine import get_engine
from pipeline.text_to_sql.table_selector import (
    TableSelectionResult,
    select_tables,
    get_focused_schema_prompt,
)
from pipeline.text_to_sql.sql_generator import (
    SQLGenerationResult,
    generate_sql,
    regenerate_sql_with_error,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MAX_EXEC_RETRIES = 2  # max times to retry after execution error

# Data coverage: earliest available quarter across all 11 tables
DATA_START_YEAR = 2015
DATA_START_PERIOD = "2015.Q1"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class QueryResult:
    query: str
    sql: str
    result: pd.DataFrame
    tables_used: list[str]
    selected_tables: list[str]
    selector_reasoning: str
    sql_attempts: int
    success: bool
    error: Optional[str] = None
    out_of_range_warning: Optional[str] = None  # set when query asks for data before DATA_START_YEAR
    chart: Optional[dict] = None                # ChartConfig for frontend, or None if not visualisable

    def __repr__(self) -> str:
        status = "✅" if self.success else "❌"
        warn = " ⚠ out-of-range" if self.out_of_range_warning else ""
        return (
            f"QueryResult({status}{warn} query={self.query!r:.50s}, "
            f"rows={len(self.result)}, tables={self.tables_used}, "
            f"sql_attempts={self.sql_attempts})"
        )

    def summary(self) -> str:
        """Human-readable summary for CLI/debugging."""
        lines = [
            f"Query   : {self.query}",
            f"Status  : {'SUCCESS' if self.success else 'FAILED'}",
            f"Tables  : {self.selected_tables} (selector) → {self.tables_used} (SQL)",
            f"Attempts: {self.sql_attempts}",
            f"SQL     :\n{self.sql}",
        ]
        if self.out_of_range_warning:
            lines.append(f"Warning : {self.out_of_range_warning}")
        if self.success:
            lines.append(f"Result  : {len(self.result)} rows × {len(self.result.columns)} cols")
            lines.append(self.result.to_string(max_rows=10, max_cols=8))
        else:
            lines.append(f"Error   : {self.error}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Out-of-range date detection
# ---------------------------------------------------------------------------

def _detect_out_of_range(query: str) -> Optional[str]:
    """
    Check if the query mentions a year that predates our data coverage (2015.Q1).
    Returns a warning string if so, else None.

    Only fires on explicit 4-digit years < DATA_START_YEAR.
    Does not fire on vague terms like 'recent', 'current', 'last year'.
    """
    years_mentioned = [int(y) for y in re.findall(r'\b(19\d{2}|20\d{2})\b', query)]
    out_of_range = [y for y in years_mentioned if y < DATA_START_YEAR]
    if out_of_range:
        years_str = ", ".join(str(y) for y in sorted(out_of_range))
        return (
            f"The query references year(s) {years_str}, but the database only covers "
            f"{DATA_START_PERIOD} onwards. Results shown are for the earliest available data."
        )
    return None


# ---------------------------------------------------------------------------
# Chart inference (Rule-based)
# ---------------------------------------------------------------------------

# Column name fragments that indicate a time-series axis
_TIME_COL_HINTS = {"period", "year", "date", "month", "quarter"}

# Column name fragments that indicate a categorical grouping axis
_CATEGORY_COL_HINTS = {
    "industry", "region", "sex", "gender", "ethnicity", "age",
    "qualification", "occupation", "sector", "group", "type",
}

# Keywords in the user question that suggest a part-of-whole / proportion chart
_PIE_QUESTION_HINTS = {
    "proportion", "share", "breakdown", "composition",
    "percentage", "percent", "split", "distribution",
}


def _classify_columns(df: pd.DataFrame) -> tuple[str | None, list[str], list[str]]:
    """
    Inspect a DataFrame and return:
      time_col    — name of the time/period column, or None
      cat_cols    — list of categorical dimension columns
      value_cols  — list of numeric value columns

    Rules are based on column name fragments, not fixed names, because
    SQL generator produces dynamic aliases (e.g. 'unemployment_rate_pct',
    'avg_hourly_earnings', 'region', 'period').
    """
    cols = df.columns.tolist()
    time_col: str | None = None
    cat_cols: list[str] = []
    value_cols: list[str] = []

    for col in cols:
        col_lower = col.lower()
        if col_lower in ("source_file",):
            continue  # internal metadata — ignore

        # Check numeric first
        if pd.api.types.is_numeric_dtype(df[col]):
            value_cols.append(col)
            continue

        # Time column — exact or fragment match
        if any(hint == col_lower or hint in col_lower for hint in _TIME_COL_HINTS):
            if time_col is None:
                time_col = col
            else:
                cat_cols.append(col)  # second period-like col → treat as category
            continue

        # Categorical column
        cat_cols.append(col)

    return time_col, cat_cols, value_cols


def infer_chart_config(df: pd.DataFrame, query: str) -> Optional[dict]:
    """
    Rule-based chart type inference. Returns a ChartConfig dict for the
    frontend, or None if the data is not suitable for visualisation.

    ChartConfig schema (TypeScript equivalent):
        {
          type:    "line" | "bar" | "grouped_bar" | "pie"
          title:   string          // auto-generated from query
          x_key:   string          // column name for X axis
          y_keys:  string[]        // column name(s) for Y axis (multi = grouped)
          y_label: string          // axis unit hint, e.g. "%" or "NZD"
          data:    object[]        // serialised DataFrame rows (JSON-safe)
        }

    Decision logic:
      1. No data / single cell → None  (show plain text)
      2. Has time column + ≥ 4 rows   → line chart (trend)
      3. Has time col + multiple value series → line chart (multi-series)
      4. Proportion keywords in query + ≤ 8 rows + 1 value col → pie chart
      5. Categorical col + ≤ 15 rows  → bar (1 value) or grouped_bar (2+ values)
      6. Fallback: bar if ≥ 2 rows, else None
    """
    if df is None or df.empty:
        return None

    n_rows = len(df)
    time_col, cat_cols, value_cols = _classify_columns(df)

    # Nothing to plot
    if not value_cols:
        return None

    # Single-cell result — a scalar answer; plain text is better
    if n_rows == 1 and len(value_cols) == 1 and not cat_cols:
        return None

    query_lower = query.lower()

    # Helper: build the data payload (drop source_file, serialise safely)
    def _make_data(subset_df: pd.DataFrame) -> list[dict]:
        drop_cols = [c for c in subset_df.columns if c.lower() == "source_file"]
        return subset_df.drop(columns=drop_cols).to_dict(orient="records")

    def _y_label(cols: list[str]) -> str:
        """Guess axis unit from column name fragments."""
        joined = " ".join(cols).lower()
        if "rate" in joined or "pct" in joined or "percent" in joined:
            return "%"
        if "earnings" in joined or "wage" in joined or "salary" in joined or "nzd" in joined:
            return "NZD"
        if "index" in joined:
            return "Index"
        if "hours" in joined:
            return "Hours"
        return ""

    def _chart_title(query: str) -> str:
        """Use first 80 chars of query, title-cased, as chart title."""
        q = query.strip().rstrip("?").strip()
        return q[:80] if len(q) <= 80 else q[:77] + "…"

    # ── Rule 1: Time series → line chart ──────────────────────────────────────
    if time_col and n_rows >= 4:
        return {
            "type": "line",
            "title": _chart_title(query),
            "x_key": time_col,
            "y_keys": value_cols,
            "y_label": _y_label(value_cols),
            "data": _make_data(df),
        }

    # ── Rule 2: Proportion / pie ───────────────────────────────────────────────
    if (
        any(hint in query_lower for hint in _PIE_QUESTION_HINTS)
        and n_rows <= 8
        and len(value_cols) == 1
        and cat_cols
    ):
        return {
            "type": "pie",
            "title": _chart_title(query),
            "x_key": cat_cols[0],
            "y_keys": value_cols,
            "y_label": _y_label(value_cols),
            "data": _make_data(df),
        }

    # ── Rule 3: Categorical comparison ────────────────────────────────────────
    # Upper limit 25: NZ industry lists have 17 entries; pie already handles ≤8
    if cat_cols and n_rows <= 25:
        chart_type = "grouped_bar" if len(value_cols) >= 2 else "bar"
        return {
            "type": chart_type,
            "title": _chart_title(query),
            "x_key": cat_cols[0],
            "y_keys": value_cols,
            "y_label": _y_label(value_cols),
            "data": _make_data(df),
        }

    # ── Rule 4: Fallback bar (e.g. no named category col but multiple rows) ───
    if n_rows >= 2 and value_cols:
        x_key = cat_cols[0] if cat_cols else (time_col or df.columns[0])
        return {
            "type": "bar",
            "title": _chart_title(query),
            "x_key": x_key,
            "y_keys": value_cols,
            "y_label": _y_label(value_cols),
            "data": _make_data(df),
        }

    return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_query(
    query: str,
    api_key: str | None = None,
    max_exec_retries: int = MAX_EXEC_RETRIES,
    verbose: bool = False,
) -> QueryResult:
    """
    Run the full text-to-SQL pipeline for a natural-language query.

    Args:
        query:            User's natural-language question.
        api_key:          Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.
        max_exec_retries: Max retries after DB execution error (default 2).
        verbose:          Print progress steps to stdout.

    Returns:
        QueryResult — always returns (never raises), with .success indicating outcome.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    _empty_df = pd.DataFrame()

    def _log(msg: str) -> None:
        if verbose:
            print(f"  [pipeline] {msg}")

    # ------------------------------------------------------------------
    # Step 1: Table selection (Haiku)
    # ------------------------------------------------------------------
    _log("Step 1: Selecting tables (Haiku)...")
    try:
        selection: TableSelectionResult = select_tables(query, api_key=key)
        _log(f"  Selected: {selection.tables}")
    except Exception as e:
        err = f"Table selection failed: {e}"
        _log(f"  ERROR: {err}")
        return QueryResult(
            query=query, sql="", result=_empty_df,
            tables_used=[], selected_tables=[],
            selector_reasoning="", sql_attempts=0,
            success=False, error=err,
        )

    # ------------------------------------------------------------------
    # Step 2: Build focused schema for selected tables
    # ------------------------------------------------------------------
    focused_schema = get_focused_schema_prompt(selection.tables)

    # ------------------------------------------------------------------
    # Step 3: SQL generation (Sonnet) + execution + retry loop
    # ------------------------------------------------------------------
    sql_result: Optional[SQLGenerationResult] = None
    exec_result: pd.DataFrame = _empty_df
    last_error: Optional[str] = None
    current_sql = ""

    for exec_attempt in range(1, max_exec_retries + 2):
        # --- Generate SQL ---
        _log(f"Step 3.{exec_attempt}: Generating SQL (Sonnet)...")
        try:
            if exec_attempt == 1:
                sql_result = generate_sql(
                    query=query,
                    selected_tables=selection.tables,
                    focused_schema=focused_schema,
                    api_key=key,
                )
            else:
                # Retry: tell Sonnet what went wrong
                _log(f"  Retrying SQL due to: {last_error}")
                sql_result = regenerate_sql_with_error(
                    query=query,
                    selected_tables=selection.tables,
                    focused_schema=focused_schema,
                    previous_sql=current_sql,
                    execution_error=last_error,
                    api_key=key,
                )
        except Exception as e:
            last_error = f"SQL generation error: {e}"
            _log(f"  ERROR: {last_error}")
            if exec_attempt > max_exec_retries:
                break
            continue

        current_sql = sql_result.sql
        _log(f"  SQL generated ({len(current_sql)} chars, attempt {sql_result.attempts})")

        # --- Execute SQL ---
        _log("  Executing SQL...")
        try:
            with get_engine() as db:
                exec_result = db.query(current_sql)
        except Exception as e:
            last_error = f"SQL execution error: {e}"
            _log(f"  ERROR: {last_error}")
            if exec_attempt > max_exec_retries:
                break
            continue

        # --- Check result is non-empty ---
        if exec_result.empty:
            last_error = "SQL executed successfully but returned 0 rows."
            _log(f"  WARNING: {last_error}")
            if exec_attempt > max_exec_retries:
                break
            continue

        # --- Success ---
        _log(f"  ✅ Result: {len(exec_result)} rows × {len(exec_result.columns)} cols")
        warning = _detect_out_of_range(query)
        if warning:
            _log(f"  ⚠ Out-of-range warning: {warning}")
        chart = infer_chart_config(exec_result, query)
        _log(f"  📊 Chart: {chart['type'] if chart else 'none'}")
        return QueryResult(
            query=query,
            sql=current_sql,
            result=exec_result,
            tables_used=sql_result.tables_used,
            selected_tables=selection.tables,
            selector_reasoning=selection.reasoning,
            sql_attempts=exec_attempt,
            success=True,
            error=None,
            out_of_range_warning=warning,
            chart=chart,
        )

    # --- All retries exhausted ---
    return QueryResult(
        query=query,
        sql=current_sql,
        result=exec_result,  # may be empty
        tables_used=sql_result.tables_used if sql_result else [],
        selected_tables=selection.tables,
        selector_reasoning=selection.reasoning,
        sql_attempts=max_exec_retries + 1,
        success=False,
        error=last_error,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "What is the current unemployment rate in New Zealand?"
    )

    print(f"\nRunning pipeline for: {query!r}\n")
    result = run_query(query, verbose=True)
    print()
    print(result.summary())
