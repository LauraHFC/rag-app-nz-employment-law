"""
pipeline/tools/employment_search.py
====================================
Tool wrapper: search_employment_law

Wraps the existing employment-law RAG pipeline (rag_retriever.py) with the
interface expected by agent_router.py.  The employment vector store is NOT
modified by the tax extension — this wrapper simply calls retrieve() and
packages the result as a ToolResult.

Public API:
    execute(inputs: dict) -> ToolResult
        inputs["query"] — retrieval query string (rephrased by the routing model)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_EMPLOYMENT_VS_DIR = _PROJECT_ROOT / "data" / "vectorstore"


# ---------------------------------------------------------------------------
# Shared result type (same shape for all three tool wrappers)
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """Structured result returned by any tool to the agent router."""
    tool_name: str
    success: bool
    chunks: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)   # [{title, url, content_type}]
    domain: str = ""                                     # "employment" | "tax" | "labour_stats"
    error: str | None = None
    raw: dict = field(default_factory=dict)              # pass-through for debugging


# ---------------------------------------------------------------------------
# Lazy singleton — mirrors the existing intelligence_hub pattern
# ---------------------------------------------------------------------------

_retriever = None


def _get_retriever():
    global _retriever
    if _retriever is None:
        from pipeline.rag_retriever import retrieve as _retrieve
        _retriever = _retrieve
    return _retriever


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------

def execute(inputs: dict) -> ToolResult:
    """
    Execute the search_employment_law tool.

    Args:
        inputs: dict with key "query" (str) — retrieval query

    Returns:
        ToolResult with chunks and sources from the employment vector store
    """
    query = inputs.get("query", "").strip()
    if not query:
        return ToolResult(
            tool_name="search_employment_law",
            success=False,
            domain="employment",
            error="Empty query — no retrieval performed.",
        )

    try:
        retrieve = _get_retriever()
        ctx = retrieve(query, n_results=5, vectorstore_path=_EMPLOYMENT_VS_DIR)

        log.info(
            "[search_employment_law] Retrieved %d chunks for: %s",
            len(ctx.chunks),
            query[:80],
        )

        return ToolResult(
            tool_name="search_employment_law",
            success=True,
            chunks=ctx.chunks,
            sources=ctx.sources,
            domain="employment",
            raw={"query": query, "n_results": len(ctx.chunks)},
        )

    except Exception as exc:
        log.exception("[search_employment_law] Retrieval error: %s", exc)
        return ToolResult(
            tool_name="search_employment_law",
            success=False,
            domain="employment",
            error=str(exc),
        )
