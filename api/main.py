# api/main.py — FastAPI wrapper around RAGSystem (§3.2 handoff doc)

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# ── Add project root to path so pipeline imports work ─────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.rag_query import RAGSystem  # noqa: E402

from api.models import (  # noqa: E402
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    QueryRequest,
    QueryResponse,
    Source,
    TopicInfo,
    TopicsResponse,
)

# ── Intelligence Hub router (Phase 3) ─────────────────────────────────────────
from api.intelligence_hub import hub_router  # noqa: E402

# ── App setup ──────────────────────────────────────────────────────────────────
app = FastAPI(
    title="NZ Employment Intelligence Hub API",
    version="2.0.0",
    description=(
        "NZ Employment Intelligence Hub. "
        "Original RAG endpoint: POST /api/query. "
        "New unified endpoint: POST /api/hub/query (auto-routes legal / data / hybrid). "
        "See /docs for interactive API explorer."
    ),
)

# ── CORS (§6.3 handoff doc) ───────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://nzlaw.linkiwise.com",
        "https://nz-employment-law-frontend.vercel.app",
        "http://localhost:3000",
        "http://localhost:3001",
    ],

    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# ── RAG system singleton ──────────────────────────────────────────────────────
# Loaded once at startup; shared across all requests.
_rag: RAGSystem | None = None

# Vectorstore path — same location used by pipeline/rag_query.py
VS_DIR = Path(__file__).parent.parent / "data" / "vectorstore"


def get_rag() -> RAGSystem:
    global _rag
    if _rag is None:
        _rag = RAGSystem(VS_DIR)
    return _rag


# ── Topic registry ────────────────────────────────────────────────────────────
# Add new topics here when the backend team onboards a new knowledge base.
# The frontend reads this dynamically — no frontend code change required.
TOPICS: list[TopicInfo] = [
    TopicInfo(
        id="nz_employment_law",
        label="Employment Law",
        description="NZ employment rights, obligations, leave, and dismissal",
        chunk_count=1960,
        active=True,
    ),
    # TopicInfo(id="health_safety", label="Health & Safety", ..., active=False),
]

# Map topic ID → ChromaDB collection name (do not rename the existing collection)
TOPIC_COLLECTION_MAP: dict[str, str] = {
    "nz_employment_law": "nz_employment_law",
}

# Feedback log path
FEEDBACK_LOG = Path(__file__).parent.parent / "data" / "feedback_log.jsonl"

# ── Mount Intelligence Hub router ─────────────────────────────────────────────
app.include_router(hub_router)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/health", response_model=HealthResponse, summary="Liveness check")
def health() -> HealthResponse:
    """Returns 200 when the vectorstore is loaded and the Claude API key is set."""
    try:
        rag = get_rag()
        chunks_loaded = rag.collection.count() if hasattr(rag, "collection") else 0
        model = getattr(rag, "model", "claude-haiku-4-5-20251001")
        api_key_set = bool(os.getenv("ANTHROPIC_API_KEY"))
        if not api_key_set:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        return HealthResponse(status="ok", chunks_loaded=chunks_loaded, model=model)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/topics", response_model=TopicsResponse, summary="List available knowledge bases")
def list_topics() -> TopicsResponse:
    """
    Returns all registered knowledge bases.
    The frontend calls this on load to populate the topic selector.
    When a new topic is added, update TOPICS above — no frontend change needed.
    """
    return TopicsResponse(topics=TOPICS)


@app.post("/api/query", response_model=QueryResponse, summary="Ask a question")
def query(req: QueryRequest) -> QueryResponse:
    """
    Submit a question to the RAG pipeline.

    - **question**: Natural language question (max 2000 chars)
    - **topic**: Knowledge base ID from GET /api/topics (required)
    - **n_results**: Number of source chunks to retrieve (default 5)
    """
    # Validate topic
    if req.topic not in TOPIC_COLLECTION_MAP:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown topic '{req.topic}'. Valid topics: {list(TOPIC_COLLECTION_MAP)}",
        )

    try:
        result = get_rag().query(req.question, n_results=req.n_results)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"RAG query failed: {exc}") from exc

    sources = [
        Source(
            title=s.get("title", ""),
            url=s.get("url", ""),
            content_type=s.get("content_type", "guide"),
            source_name=s.get("source_name", ""),
        )
        for s in result.get("sources", [])
    ]

    return QueryResponse(
        answer=result.get("answer", ""),
        sources=sources,
        question=result.get("question", req.question),
    )


@app.post("/api/feedback", response_model=FeedbackResponse, summary="Log user feedback")
def feedback(req: FeedbackRequest) -> FeedbackResponse:
    """
    Log a thumbs-up or thumbs-down rating for an answer.
    Appends to data/feedback_log.jsonl — one JSON object per line.
    """
    try:
        FEEDBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "question": req.question,
            "rating": req.rating,
            "topic": req.topic,
        }
        with FEEDBACK_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        # Best-effort — log but don't fail the request
        print(f"[feedback] Failed to write log: {exc}", flush=True)

    return FeedbackResponse(status="logged")
