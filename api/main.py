# api/main.py — FastAPI wrapper
# v2: risk-control pipeline wired in (intent classifier, crisis detector,
#     output guard, consent_events audit, answer_audit).

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

# ── Add project root to path so pipeline imports work ─────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.rag_query import RAGSystem  # noqa: E402

from api.models import (  # noqa: E402
    AgentQueryRequest,
    AgentQueryResponse,
    AgentSource,
    ConsentAcknowledgeRequest,
    ConsentAcknowledgeResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    QueryRequest,
    QueryResponse,
    Source,
    TopicInfo,
    TopicsResponse,
)
from api.db import init_db, log_consent_event, log_answer_audit  # noqa: E402

# ── Intelligence Hub router (Phase 3) ─────────────────────────────────────────
from api.intelligence_hub import hub_router  # noqa: E402


# ── App setup ──────────────────────────────────────────────────────────────────
app = FastAPI(
    title="NZ Employment & Tax Law API",
    version="4.0.0",
    description=(
        "NZ Employment & Tax Law Intelligence Hub. "
        "v4: full risk-control pipeline (crisis detection, intent classification, "
        "output guard, consent audit). "
        "Agent endpoint: POST /api/agent/query. "
        "Consent endpoint: POST /api/consent/acknowledge. "
        "See /docs for interactive API explorer."
    ),
)

# ── CORS ───────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://nzlaw.linkiwise.com",
        "https://nz-employment-law-frontend.vercel.app",
        "http://localhost:3000",
        "http://localhost:3001",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
)

# ── Vector store bootstrap (download from R2 if missing) ──────────────────────
def _ensure_tax_vectorstore() -> None:
    """
    Ensure the tax vector store is present locally.

    The tax vector store is too large to ship in git (>100 MB). It is hosted
    on Cloudflare R2 and downloaded once on first boot, then cached on the
    container's local disk for the remainder of that container's lifetime.

    Controlled via env var TAX_VECTORSTORE_URL (e.g. a public R2 URL pointing
    to a .tar.gz of the vectorstore_tax directory). If the env var is unset,
    this function is a no-op (useful for local dev where the dir already
    exists).
    """
    import logging
    log = logging.getLogger(__name__)

    project_root = Path(__file__).parent.parent
    tax_dir = project_root / "data" / "vectorstore_tax"
    sentinel = tax_dir / "chroma.sqlite3"

    # Already present and looks real (>1 KB rules out an LFS pointer file)
    if sentinel.exists() and sentinel.stat().st_size > 1024:
        log.info("Tax vector store already present at %s", tax_dir)
        return

    url = os.environ.get("TAX_VECTORSTORE_URL")
    if not url:
        log.warning(
            "TAX_VECTORSTORE_URL not set and tax vector store missing — "
            "tax queries will fail until vectorstore is provisioned."
        )
        return

    log.info("Downloading tax vector store from %s ...", url)
    import urllib.request, tarfile, tempfile

    tax_dir.parent.mkdir(parents=True, exist_ok=True)
    # Wipe any partial/LFS-pointer state
    if tax_dir.exists():
        import shutil
        shutil.rmtree(tax_dir)

    # Cloudflare R2's pub-*.r2.dev development URLs reject the default
    # Python-urllib User-Agent (returns 403). Send a browser-like UA instead.
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; nzlaw-api/1.0)"},
    )
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        with urllib.request.urlopen(req) as resp:
            while chunk := resp.read(1024 * 1024):  # 1 MB chunks
                tmp.write(chunk)
        tmp.flush()
        log.info("Downloaded %d bytes, extracting...", Path(tmp.name).stat().st_size)
        with tarfile.open(tmp.name, "r:gz") as tar:
            tar.extractall(path=project_root / "data")
        Path(tmp.name).unlink(missing_ok=True)

    log.info("Tax vector store ready at %s", tax_dir)


# ── Startup ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup() -> None:
    """Initialise audit DB and download tax vector store on startup."""
    try:
        init_db()
    except Exception as exc:
        # Log but do not crash the app — audit DB failure should not block serving
        import logging
        logging.getLogger(__name__).error("Audit DB init failed: %s", exc)

    try:
        _ensure_tax_vectorstore()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error(
            "Tax vector store bootstrap failed: %s — tax queries will fail.", exc
        )


# ── RAG system singleton ──────────────────────────────────────────────────────
_rag: RAGSystem | None = None
VS_DIR = Path(__file__).parent.parent / "data" / "vectorstore"

def get_rag() -> RAGSystem:
    global _rag
    if _rag is None:
        _rag = RAGSystem(VS_DIR)
    return _rag


# ── Topic registry ────────────────────────────────────────────────────────────
TOPICS: list[TopicInfo] = [
    TopicInfo(
        id="nz_employment_law",
        label="Employment Law",
        description="NZ employment rights, obligations, leave, and dismissal",
        chunk_count=1960,
        active=True,
    ),
]
TOPIC_COLLECTION_MAP: dict[str, str] = {
    "nz_employment_law": "nz_employment_law",
}
FEEDBACK_LOG = Path(__file__).parent.parent / "data" / "feedback_log.jsonl"

# Mount Intelligence Hub router
app.include_router(hub_router)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/health", response_model=HealthResponse, summary="Liveness check")
def health() -> HealthResponse:
    try:
        rag = get_rag()
        chunks_loaded = rag.collection.count() if hasattr(rag, "collection") else 0
        model = getattr(rag, "model", "claude-haiku-4-5-20251001")
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        return HealthResponse(status="ok", chunks_loaded=chunks_loaded, model=model)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/topics", response_model=TopicsResponse, summary="List available knowledge bases")
def list_topics() -> TopicsResponse:
    return TopicsResponse(topics=TOPICS)


@app.post("/api/query", response_model=QueryResponse, summary="Ask a question (legacy RAG)")
def query(req: QueryRequest) -> QueryResponse:
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


# ── Consent acknowledgement ───────────────────────────────────────────────────

@app.post(
    "/api/consent/acknowledge",
    response_model=ConsentAcknowledgeResponse,
    summary="Log disclaimer modal acknowledgement or decline",
    tags=["Consent"],
)
def consent_acknowledge(
    body: ConsentAcknowledgeRequest,
    request: Request,
) -> ConsentAcknowledgeResponse:
    """
    Called by the frontend when the user clicks "I accept" or "I disagree"
    on the first-message disclaimer modal.

    - IP is hashed server-side with a rotating monthly salt.
    - The raw IP is never stored.
    - Returns the generated event_id for client-side correlation if needed.
    """
    ip_raw = request.client.host if request.client else None

    event_id = log_consent_event(
        session_id=body.session_id,
        event_type=body.event_type,
        checkbox_states=body.checkbox_states,
        user_id=body.user_id,
        user_agent=body.user_agent,
        ip_raw=ip_raw,
        ui_locale=body.ui_locale,
        question_hash=body.question_hash,
        disclaimer_version=body.disclaimer_version,
        privacy_policy_version=body.privacy_policy_version,
    )
    return ConsentAcknowledgeResponse(ok=True, event_id=event_id)


# ── Agent query ───────────────────────────────────────────────────────────────

@app.post(
    "/api/agent/query",
    response_model=AgentQueryResponse,
    summary="Ask a question (employment + tax, full risk-control pipeline)",
    tags=["Agent"],
)
def agent_query(req: AgentQueryRequest) -> AgentQueryResponse:
    """
    Submit a question to the v2 agent pipeline.

    Pipeline stages:
    1. Pre-flight regex refusal (legacy belt-and-suspenders, fast)
    2. Crisis detector (self-harm / family violence → crisis card;
       immigration / criminal → REFUSE_WITH_REFERRAL; imminent → HIGH_STAKES)
    3. Intent + domain classifier (Haiku, LOOKUP / ADVICE / HIGH_STAKES × tier)
    4. Routing matrix (domain_tier × intent_class → template)
    5. Tool routing (Sonnet, selects employment / tax / labour_stats tools)
    6. Parallel retrieval
    7. Template-aware synthesis (Haiku)
    8. Output guard (banned phrases, section headings, risk footer)
    9. Answer audit logging

    All refused questions return `refused: true` with a warm referral message.
    """
    from pipeline.refusal_router import should_refuse
    from pipeline.citation_validator import validate
    from pipeline.agent_router import run as agent_run, SYNTHESIS_MODEL

    # ── Stage 1: pre-flight regex refusal (fast, no API call) ────────────────
    refusal = should_refuse(req.question)
    if refusal.refused:
        log_answer_audit(
            intent_class="ADVICE",
            intent_confidence=1.0,
            domain_tier="M",
            domain_label="other",
            routing_outcome="REFUSE_WITH_REFERRAL",
            model="refusal_router",
            citations=[],
            banned_phrase_hits=[],
            regeneration_count=0,
            refused=True,
            crisis_route_fired=False,
        )
        return AgentQueryResponse(
            answer=refusal.message,
            sources=[],
            domains_used=[],
            tool_calls=[],
            refused=True,
            refusal_reason=refusal.reason,
            chart=None,
            question=req.question,
            intent_class="ADVICE",
            domain_tier="M",
            domain_label="other",
            routing_outcome="REFUSE_WITH_REFERRAL",
            crisis_route_fired=False,
            regeneration_count=0,
            risk_badge="refused",
        )

    # ── Stages 2–8: full agent pipeline ──────────────────────────────────────
    try:
        result = agent_run(
            question=req.question,
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent pipeline failed: {exc}") from exc

    # ── Stage 9: answer audit ────────────────────────────────────────────────
    log_answer_audit(
        intent_class=result.intent_class,
        intent_confidence=result.classifier_confidence,
        domain_tier=result.domain_tier,
        domain_label=result.domain_label,
        routing_outcome=result.routing_outcome,
        model=SYNTHESIS_MODEL,
        citations=[
            {"title": s.get("title", ""), "url": s.get("url", "")}
            for s in result.sources
        ],
        banned_phrase_hits=result.banned_phrase_hits,
        regeneration_count=result.regeneration_count,
        refused=result.refused,
        crisis_route_fired=result.crisis_route_fired,
    )

    # ── Citation URL allowlist validation ────────────────────────────────────
    raw_sources = [
        {
            "title": s.get("title", ""),
            "url": s.get("url", ""),
            "content_type": s.get("content_type", "guide"),
        }
        for s in result.sources
    ]
    validated = validate(raw_sources)
    sources = [
        AgentSource(
            title=s["title"],
            url=s["url"],
            content_type=s.get("content_type", "guide"),
        )
        for s in validated.valid_sources
    ]

    return AgentQueryResponse(
        answer=result.answer,
        sources=sources,
        domains_used=result.domains_used,
        tool_calls=result.tool_calls,
        refused=result.refused,
        refusal_reason=result.refusal_reason,
        chart=result.chart,
        question=req.question,
        intent_class=result.intent_class,
        domain_tier=result.domain_tier,
        domain_label=result.domain_label,
        routing_outcome=result.routing_outcome,
        crisis_route_fired=result.crisis_route_fired,
        regeneration_count=result.regeneration_count,
        risk_badge=result.risk_badge,
    )


# ── Feedback ──────────────────────────────────────────────────────────────────

@app.post("/api/feedback", response_model=FeedbackResponse, summary="Log user feedback")
def feedback(req: FeedbackRequest) -> FeedbackResponse:
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
        print(f"[feedback] Failed to write log: {exc}", flush=True)
    return FeedbackResponse(status="logged")
