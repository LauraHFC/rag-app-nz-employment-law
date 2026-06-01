# api/main.py — FastAPI wrapper
# v2: risk-control pipeline wired in (intent classifier, crisis detector,
#     output guard, consent_events audit, answer_audit).
# v3: Langfuse observability added (Sprint 5). trace_id in AgentQueryResponse.

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
from api.observability import (  # noqa: E402
    get_trace_id,
    observe,
    eval_trace_context,
    flush as _langfuse_flush,
)

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

    Controlled via env vars:
      - TAX_VECTORSTORE_URL — public R2 URL pointing to a .tar.gz of the
        vectorstore_tax directory. If unset, this function is a no-op
        (useful for local dev where the dir already exists).
      - TAX_VECTORSTORE_VERSION — an arbitrary version string (e.g. "v2",
        "2026-05-07-clean"). When this changes, the existing on-disk
        vectorstore is wiped and re-downloaded. This is the mechanism for
        forcing Railway to pick up a new tar.gz after replacing it on R2.
        If unset, falls back to legacy behaviour: download only when missing.
    """
    import logging
    log = logging.getLogger(__name__)

    project_root = Path(__file__).parent.parent
    tax_dir = project_root / "data" / "vectorstore_tax"
    sentinel = tax_dir / "chroma.sqlite3"
    version_file = tax_dir / ".version"

    expected_version = os.environ.get("TAX_VECTORSTORE_VERSION")

    # Decide whether the existing on-disk copy is acceptable.
    on_disk_ok = sentinel.exists() and sentinel.stat().st_size > 1024
    version_match = True
    if expected_version is not None:
        actual_version = (
            version_file.read_text().strip()
            if version_file.exists()
            else None
        )
        version_match = actual_version == expected_version
        if on_disk_ok and not version_match:
            log.info(
                "Tax vector store version mismatch (have=%r, want=%r) — "
                "wiping and re-downloading.",
                actual_version, expected_version,
            )

    if on_disk_ok and version_match:
        log.info(
            "Tax vector store already present at %s (version=%s)",
            tax_dir, expected_version or "unversioned",
        )
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
    # Wipe any partial/LFS-pointer/old-version state
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

    # Stamp the version so future restarts know what's on disk.
    if expected_version is not None:
        try:
            version_file.write_text(expected_version)
        except Exception as exc:
            log.warning("Failed to write .version file: %s", exc)

    log.info(
        "Tax vector store ready at %s (version=%s)",
        tax_dir, expected_version or "unversioned",
    )


# ── Tenancy vector store bootstrap ─────────────────────────────────────────────
def _ensure_tenancy_vectorstore() -> None:
    """
    Ensure the tenancy vector store is present locally.

    Same pattern as _ensure_tax_vectorstore(): hosted on Cloudflare R2,
    downloaded once on first boot.

    Env vars:
      - TENANCY_VECTORSTORE_URL — public R2 URL to vectorstore_tenancy.tar.gz
      - TENANCY_VECTORSTORE_VERSION — version string for cache-busting
    """
    import logging
    log = logging.getLogger(__name__)

    project_root = Path(__file__).parent.parent
    tenancy_dir = project_root / "data" / "vectorstore_tenancy"
    sentinel = tenancy_dir / "chroma.sqlite3"
    version_file = tenancy_dir / ".version"

    expected_version = os.environ.get("TENANCY_VECTORSTORE_VERSION")

    on_disk_ok = sentinel.exists() and sentinel.stat().st_size > 1024
    version_match = True
    if expected_version is not None:
        actual_version = (
            version_file.read_text().strip()
            if version_file.exists()
            else None
        )
        version_match = actual_version == expected_version
        if on_disk_ok and not version_match:
            log.info(
                "Tenancy vector store version mismatch (have=%r, want=%r) — "
                "wiping and re-downloading.",
                actual_version, expected_version,
            )

    if on_disk_ok and version_match:
        log.info(
            "Tenancy vector store already present at %s (version=%s)",
            tenancy_dir, expected_version or "unversioned",
        )
        return

    url = os.environ.get("TENANCY_VECTORSTORE_URL")
    if not url:
        log.warning(
            "TENANCY_VECTORSTORE_URL not set and tenancy vector store missing — "
            "tenancy queries will fail until vectorstore is provisioned."
        )
        return

    log.info("Downloading tenancy vector store from %s ...", url)
    import urllib.request, tarfile, tempfile

    tenancy_dir.parent.mkdir(parents=True, exist_ok=True)
    if tenancy_dir.exists():
        import shutil
        shutil.rmtree(tenancy_dir)

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; nzlaw-api/1.0)"},
    )
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        with urllib.request.urlopen(req) as resp:
            while chunk := resp.read(1024 * 1024):
                tmp.write(chunk)
        tmp.flush()
        log.info("Downloaded %d bytes, extracting...", Path(tmp.name).stat().st_size)
        with tarfile.open(tmp.name, "r:gz") as tar:
            tar.extractall(path=project_root / "data")
        Path(tmp.name).unlink(missing_ok=True)

    if expected_version is not None:
        try:
            version_file.write_text(expected_version)
        except Exception as exc:
            log.warning("Failed to write .version file: %s", exc)

    log.info(
        "Tenancy vector store ready at %s (version=%s)",
        tenancy_dir, expected_version or "unversioned",
    )


def _ensure_consumer_vectorstore() -> None:
    """
    Ensure the consumer protection vector store is present locally.

    Same pattern as _ensure_tenancy_vectorstore(): hosted on Cloudflare R2,
    downloaded once on first boot.

    Env vars:
      - CONSUMER_VECTORSTORE_URL — public R2 URL to vectorstore_consumer.tar.gz
      - CONSUMER_VECTORSTORE_VERSION — version string for cache-busting
    """
    import logging
    log = logging.getLogger(__name__)

    project_root = Path(__file__).parent.parent
    consumer_dir = project_root / "data" / "vectorstore_consumer"
    sentinel = consumer_dir / "chroma.sqlite3"
    version_file = consumer_dir / ".version"

    expected_version = os.environ.get("CONSUMER_VECTORSTORE_VERSION")

    on_disk_ok = sentinel.exists() and sentinel.stat().st_size > 1024
    version_match = True
    if expected_version is not None:
        actual_version = (
            version_file.read_text().strip()
            if version_file.exists()
            else None
        )
        version_match = actual_version == expected_version
        if on_disk_ok and not version_match:
            log.info(
                "Consumer vector store version mismatch (have=%r, want=%r) — "
                "wiping and re-downloading.",
                actual_version, expected_version,
            )

    if on_disk_ok and version_match:
        log.info(
            "Consumer vector store already present at %s (version=%s)",
            consumer_dir, expected_version or "unversioned",
        )
        return

    url = os.environ.get("CONSUMER_VECTORSTORE_URL")
    if not url:
        log.warning(
            "CONSUMER_VECTORSTORE_URL not set and consumer vector store missing — "
            "consumer queries will fail until vectorstore is provisioned."
        )
        return

    log.info("Downloading consumer vector store from %s ...", url)
    import urllib.request, tarfile, tempfile

    consumer_dir.parent.mkdir(parents=True, exist_ok=True)
    if consumer_dir.exists():
        import shutil
        shutil.rmtree(consumer_dir)

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; nzlaw-api/1.0)"},
    )
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        with urllib.request.urlopen(req) as resp:
            while chunk := resp.read(1024 * 1024):
                tmp.write(chunk)
        tmp.flush()
        log.info("Downloaded %d bytes, extracting...", Path(tmp.name).stat().st_size)
        with tarfile.open(tmp.name, "r:gz") as tar:
            tar.extractall(path=project_root / "data")
        Path(tmp.name).unlink(missing_ok=True)

    if expected_version is not None:
        try:
            version_file.write_text(expected_version)
        except Exception as exc:
            log.warning("Failed to write .version file: %s", exc)

    log.info(
        "Consumer vector store ready at %s (version=%s)",
        consumer_dir, expected_version or "unversioned",
    )


# ── Startup ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup() -> None:
    """Initialise audit DB and download vector stores on startup."""
    try:
        init_db()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("Audit DB init failed: %s", exc)

    try:
        _ensure_tax_vectorstore()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error(
            "Tax vector store bootstrap failed: %s — tax queries will fail.", exc
        )

    try:
        _ensure_tenancy_vectorstore()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error(
            "Tenancy vector store bootstrap failed: %s — tenancy queries will fail.", exc
        )

    try:
        _ensure_consumer_vectorstore()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error(
            "Consumer vector store bootstrap failed: %s — consumer queries will fail.", exc
        )


# ── Shutdown ───────────────────────────────────────────────────────────────────
@app.on_event("shutdown")
def shutdown() -> None:
    """Flush Langfuse batch exporter before process exit.

    Without this, the OpenTelemetry batch exporter that ships traces to
    Langfuse can lose any events still in its in-memory queue when the
    server is Ctrl-C'd. This is why the Sprint 5 v2 baseline run
    (2026-05-16) only saw 2/36 traces in Langfuse despite all 36 returning
    HTTP 200 locally.
    """
    try:
        _langfuse_flush()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "[observability] flush on shutdown failed (non-fatal): %s", exc
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
    summary="Ask a question (employment + tax, Sprint 6 slim pipeline)",
    tags=["Agent"],
)
def agent_query(req: AgentQueryRequest, request: Request) -> AgentQueryResponse:
    """
    Submit a question to the agent pipeline (Sprint 6 slimmed).

    Pipeline stages:
    1. Crisis detector (self-harm / family violence → crisis card or REFUSE)
    2. Pre-flight regex refusal (narrow 4-category set:
       active_proceeding / immigration / criminal / wills_estates)
    3. Tool routing (Sonnet picks employment / tax / labour_stats tools)
    4. Parallel retrieval
    5. Unified synthesis (Haiku — one prompt, shape follows complexity)
    6. Output guard (banned phrases + LIGHT footer)
    7. Answer audit logging

    Sprint 6 removed: intent classifier, 15-cell ROUTING_MATRIX, 4 synthesis
    templates, mandatory 7-section headings, tiered footers, risk_badge tiers.
    All refused questions return `refused: true` with a warm referral message
    and the UI renders the risk badge only in that case.
    """
    from pipeline.citation_validator import validate
    from pipeline.agent_router import run as agent_run, SYNTHESIS_MODEL

    # The pre-flight refusal step is now part of the agent_router pipeline
    # itself (Sprint 6) — main.py no longer duplicates it.

    # ── Eval tagging: open the propagate_attributes scope BEFORE any pipeline
    #    work. The context manager reads X-Eval-* headers; if present, it
    #    propagates env=eval + run/question/pipeline/outcome/difficulty tags
    #    to every @observe span created inside this block. Production requests
    #    (no X-Eval-* headers) hit a transparent no-op. ───────────────────────
    with eval_trace_context(request.headers):
        # ── Run the full agent pipeline ──────────────────────────────────────
        try:
            result = agent_run(question=req.question)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Agent pipeline failed: {exc}") from exc

        # ── Answer audit (slim — Sprint 6) ───────────────────────────────────
        log_answer_audit(
            domain_label=result.domain_label,
            model=SYNTHESIS_MODEL,
            citations=[
                {"title": s.get("title", ""), "url": s.get("url", "")}
                for s in result.sources
            ],
            banned_phrase_hits=result.banned_phrase_hits,
            regeneration_count=result.regeneration_count,
            refused=result.refused,
            refusal_reason=result.refusal_reason,
            crisis_route_fired=result.crisis_route_fired,
        )

        # ── Citation URL allowlist validation ────────────────────────────────
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

        # NOTE: get_trace_id() must be called INSIDE the propagate_attributes
        # context, before all @observe spans have detached. Once the `with`
        # block ends the OTel context is torn down and the helper returns None.
        captured_trace_id = get_trace_id()

    return AgentQueryResponse(
        answer=result.answer,
        sources=sources,
        domains_used=result.domains_used,
        tool_calls=result.tool_calls,
        refused=result.refused,
        refusal_reason=result.refusal_reason,
        chart=result.chart,
        question=req.question,
        domain_label=result.domain_label,
        crisis_route_fired=result.crisis_route_fired,
        regeneration_count=result.regeneration_count,
        trace_id=captured_trace_id,
    )


# ── Feedback ──────────────────────────────────────────────────────────────────

@app.post("/api/feedback", response_model=FeedbackResponse, summary="Log user feedback")
def feedback(req: FeedbackRequest) -> FeedbackResponse:
    """
    Log user thumbs-up / thumbs-down feedback against a Langfuse trace.

    The frontend sends the trace_id it received from /api/agent/query so
    the score is attached to the exact span that produced the answer.

    Belt-and-braces: also writes to the local JSONL as a fallback, but
    this file is ephemeral on Railway — Langfuse is the durable store.
    """
    from api.observability import _langfuse_enabled, _lf_get_client  # local import

    score_value = 1 if req.rating == "up" else -1

    # ── Primary: Langfuse score ────────────────────────────────────────────
    if _langfuse_enabled and _lf_get_client is not None:
        try:
            _lf_get_client().score(
                trace_id=req.trace_id,
                name="user_feedback",
                value=score_value,
                comment=req.comment or "",
            )
        except Exception as exc:
            print(f"[feedback] Langfuse score failed: {exc}", flush=True)

    # ── Belt-and-braces: local JSONL (ephemeral on Railway, safe to lose) ──
    try:
        FEEDBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_id": req.trace_id,
            "rating": req.rating,
            "comment": req.comment,
        }
        with FEEDBACK_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        print(f"[feedback] JSONL write failed (non-critical): {exc}", flush=True)

    return FeedbackResponse(status="logged")
