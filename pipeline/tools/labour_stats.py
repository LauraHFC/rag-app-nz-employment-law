"""
pipeline/tools/labour_stats.py
================================
Tool wrapper: query_labour_market_stats

Wraps the existing Intelligence Hub text-to-SQL pipeline
(pipeline/text_to_sql/query_pipeline.py).  The existing 3-class query_router.py
continues to handle internal SQL/RAG/Hybrid routing within this tool — it is NOT
replaced by the top-level agent router.

Public API:
    execute(inputs: dict) -> ToolResult
        inputs["question"] — natural-language statistical query
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pipeline.tools.employment_search import ToolResult  # reuse shared dataclass

log = logging.getLogger(__name__)


def execute(inputs: dict) -> ToolResult:
    """
    Execute the query_labour_market_stats tool.

    Args:
        inputs: dict with key "question" (str) — natural-language query
                about NZ labour market statistics

    Returns:
        ToolResult with chunks (formatted as text) and no URL sources
        (data comes from DuckDB, not web pages)
    """
    question = inputs.get("question", "").strip()
    if not question:
        return ToolResult(
            tool_name="query_labour_market_stats",
            success=False,
            domain="labour_stats",
            error="Empty question — no query executed.",
        )

    try:
        from pipeline.text_to_sql.query_pipeline import run_query

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        result = run_query(question, api_key=api_key)

        if not result.success:
            log.warning(
                "[query_labour_market_stats] Pipeline failed for: %s — %s",
                question[:80],
                result.error,
            )
            return ToolResult(
                tool_name="query_labour_market_stats",
                success=False,
                domain="labour_stats",
                error=result.error or "SQL pipeline returned no results.",
            )

        # Convert DataFrame to text chunks for the synthesis model
        df_text = result.result.to_markdown(index=False) if not result.result.empty else ""
        chunks = [df_text] if df_text else []

        # Stats results have no web-page sources — cite the dataset instead
        sources = [
            {
                "title": "NZ Labour Market Statistics (Stats NZ / MBIE)",
                "url": "https://www.mbie.govt.nz/business-and-employment/employment-and-skills/labour-market-reports-data-and-analysis/",
                "content_type": "statistics",
            }
        ]

        # Surface any out-of-range warning as a chunk for synthesis
        if result.out_of_range_warning:
            chunks.append(f"[Warning] {result.out_of_range_warning}")

        log.info(
            "[query_labour_market_stats] SQL succeeded for: %s (%d rows)",
            question[:80],
            len(result.result),
        )

        return ToolResult(
            tool_name="query_labour_market_stats",
            success=True,
            chunks=chunks,
            sources=sources,
            domain="labour_stats",
            raw={
                "question": question,
                "sql": result.sql,
                "tables_used": result.tables_used,
                "rows": len(result.result),
                "chart": result.chart,
                "out_of_range_warning": result.out_of_range_warning,
            },
        )

    except ImportError as exc:
        # Distinguishes env / dependency problems from runtime SQL errors.
        # Without this branch, a missing duckdb (or other dep) surfaces as a
        # generic "SQL execution error: No module named X", which is hard to
        # tell apart from a real SQL failure in eval traces.
        log.error(
            "[query_labour_market_stats] DEPENDENCY MISSING — labour_stats "
            "cannot run. Install requirements.txt in the API server venv. "
            "Detail: %s", exc,
        )
        return ToolResult(
            tool_name="query_labour_market_stats",
            success=False,
            domain="labour_stats",
            error=f"Labour-market dependency missing: {exc}. "
                  f"Run `pip install -r requirements.txt` in the API server venv.",
        )

    except Exception as exc:
        log.exception("[query_labour_market_stats] Unexpected error: %s", exc)
        return ToolResult(
            tool_name="query_labour_market_stats",
            success=False,
            domain="labour_stats",
            error=str(exc),
        )
