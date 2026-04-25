"""
intelligence_hub.py
===================
FastAPI router for the NZ Employment Intelligence Hub.

Exposes a single endpoint:
    POST /api/hub/query

This endpoint sits on top of the two existing pipelines:
    - RAG     (pipeline/rag_query.py)      — NZ employment law
    - SQL     (pipeline/text_to_sql/)      — Stats NZ labour market data

Flow:
    user query
        → QueryRouter (Haiku)    — classifies intent: legal / data / hybrid
        → legal  → RAGSystem.query()
          data   → run_query()
          hybrid → both in parallel → Haiku answer synthesiser
        → HubResponse

Designed to be mounted into api/main.py via:
    from api.intelligence_hub import hub_router
    app.include_router(hub_router)

The original /api/query endpoint is left untouched.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# ── Project root path trick (works whether imported from api/ or root) ─────────
import sys
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from pipeline.query_router import RouteResult, classify_query
from pipeline.text_to_sql.query_pipeline import QueryResult, run_query
from pipeline.rag_retriever import RAGContext, retrieve as rag_retrieve
from pipeline.answer_generator import RetrievalContext, GeneratedAnswer, generate as generate_answer

# ---------------------------------------------------------------------------
# Router setup
# ---------------------------------------------------------------------------

hub_router = APIRouter(prefix="/api/hub", tags=["Intelligence Hub"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class HubQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000,
                          description="Natural language question")
    n_results: int = Field(default=5, ge=1, le=20,
                           description="Number of RAG chunks to retrieve (legal path only)")


class LegalSource(BaseModel):
    title: str
    url: str
    content_type: str = "guide"


class HubQueryResponse(BaseModel):
    question: str
    intent: str                          # "legal" | "data" | "hybrid"
    confidence: str                      # "high" | "medium" | "low"
    answer: str
    sources: list[LegalSource] = []      # populated for legal / hybrid
    data_sql: Optional[str] = None       # populated for data / hybrid (debug)
    data_rows: Optional[int] = None      # row count of SQL result
    out_of_range_warning: Optional[str] = None
    router_reasoning: str = ""
    chart: Optional[dict] = None         # ChartConfig for frontend visualisation, or None


# ---------------------------------------------------------------------------
# Helper: build HubQueryResponse from GeneratedAnswer
# ---------------------------------------------------------------------------

def _build_response(
    req: HubQueryRequest,
    route: RouteResult,
    gen: GeneratedAnswer,
    sources: list[LegalSource] | None = None,
    sql_result: QueryResult | None = None,
) -> HubQueryResponse:
    """Assemble the final response from generated answer + metadata."""
    return HubQueryResponse(
        question=req.question,
        intent=route.intent,
        confidence=route.confidence,
        answer=gen.answer,
        sources=sources or [],
        data_sql=sql_result.sql if sql_result else None,
        data_rows=len(sql_result.result) if sql_result and sql_result.success else None,
        out_of_range_warning=sql_result.out_of_range_warning if sql_result else None,
        router_reasoning=route.reasoning,
        chart=gen.chart,
    )


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------

@hub_router.post("/query", response_model=HubQueryResponse, summary="Intelligence Hub query")
def hub_query(req: HubQueryRequest) -> HubQueryResponse:
    """
    Unified query endpoint for the NZ Employment Intelligence Hub.

    Automatically routes the question to:
    - **legal**  → RAG pipeline (NZ employment law documents)
    - **data**   → Text-to-SQL pipeline (Stats NZ labour market data)
    - **hybrid** → both pipelines, synthesised into one answer

    No topic selection needed — the router classifies intent automatically.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set")

    # ------------------------------------------------------------------
    # Step 1: Classify intent
    # ------------------------------------------------------------------
    route: RouteResult = classify_query(req.question, api_key=api_key)

    # ------------------------------------------------------------------
    # Step 2: Route to pipeline(s)
    # ------------------------------------------------------------------

    if route.intent == "legal":
        return _handle_legal(req, route, api_key)

    elif route.intent == "data":
        return _handle_data(req, route, api_key)

    else:  # hybrid
        return _handle_hybrid(req, route, api_key)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def _handle_legal(
    req: HubQueryRequest,
    route: RouteResult,
    api_key: str,
) -> HubQueryResponse:
    """Retrieve legal chunks → unified answer generator."""
    try:
        rag_ctx: RAGContext = rag_retrieve(req.question, n_results=req.n_results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG pipeline error: {e}")

    sources = [
        LegalSource(title=s["title"], url=s["url"], content_type=s.get("content_type", "guide"))
        for s in rag_ctx.sources
    ]

    ctx = RetrievalContext(
        question=req.question,
        legal_chunks=rag_ctx.chunks,
        legal_metadatas=rag_ctx.metadatas,
    )
    gen = generate_answer(ctx, api_key=api_key)
    return _build_response(req, route, gen, sources=sources)


def _handle_data(
    req: HubQueryRequest,
    route: RouteResult,
    api_key: str,
) -> HubQueryResponse:
    """Run SQL pipeline → unified answer generator."""
    sql_result: QueryResult = run_query(req.question, api_key=api_key)

    if not sql_result.success:
        return HubQueryResponse(
            question=req.question,
            intent=route.intent,
            confidence=route.confidence,
            answer=(
                "I was unable to retrieve statistical data for your question. "
                f"Details: {sql_result.error}"
            ),
            router_reasoning=route.reasoning,
            out_of_range_warning=sql_result.out_of_range_warning,
        )

    ctx = RetrievalContext(
        question=req.question,
        sql_dataframe=sql_result.result,
        sql_query=sql_result.sql,
        tables_used=sql_result.tables_used,
        out_of_range_warning=sql_result.out_of_range_warning,
        chart_hint=sql_result.chart,
    )
    gen = generate_answer(ctx, api_key=api_key)
    return _build_response(req, route, gen, sql_result=sql_result)


def _handle_hybrid(
    req: HubQueryRequest,
    route: RouteResult,
    api_key: str,
) -> HubQueryResponse:
    """Run both pipelines → unified answer generator."""

    # Retrieve legal — catch individually so one failure doesn't kill both
    rag_ctx: Optional[RAGContext] = None
    sql_result: Optional[QueryResult] = None

    try:
        rag_ctx = rag_retrieve(req.question, n_results=req.n_results)
    except Exception as e:
        pass  # legal retrieval failed; data may still work

    sql_result = run_query(req.question, api_key=api_key)

    # Build unified context
    ctx = RetrievalContext(
        question=req.question,
        legal_chunks=rag_ctx.chunks if rag_ctx else None,
        legal_metadatas=rag_ctx.metadatas if rag_ctx else None,
        sql_dataframe=sql_result.result if sql_result and sql_result.success else None,
        sql_query=sql_result.sql if sql_result else None,
        tables_used=sql_result.tables_used if sql_result else [],
        out_of_range_warning=sql_result.out_of_range_warning if sql_result else None,
        chart_hint=sql_result.chart if sql_result and sql_result.success else None,
    )
    gen = generate_answer(ctx, api_key=api_key)

    # Collect sources from RAG
    sources = []
    if rag_ctx:
        sources = [
            LegalSource(title=s["title"], url=s["url"], content_type=s.get("content_type", "guide"))
            for s in rag_ctx.sources
        ]

    return _build_response(req, route, gen, sources=sources, sql_result=sql_result)
