# api/models.py — Pydantic request/response models
# v2: risk-control fields added to AgentQueryResponse; ConsentAcknowledgeRequest added.
# v3: trace_id added to AgentQueryResponse; FeedbackRequest updated for Langfuse scores.

from pydantic import BaseModel, Field
from typing import Any, Literal


# ── Request models ─────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    topic: str = Field(..., description="Knowledge base ID, e.g. 'nz_employment_law'")
    n_results: int = Field(default=5, ge=1, le=20)


class FeedbackRequest(BaseModel):
    trace_id: str = Field(..., min_length=1, description="Langfuse trace ID from the query response")
    rating: Literal["up", "down"]
    comment: str | None = Field(None, max_length=1000)


# ── Response models ────────────────────────────────────────────────────────────

class Source(BaseModel):
    title: str
    url: str
    content_type: Literal["guide", "legislation", "case"] = "guide"
    source_name: str = ""


class QueryResponse(BaseModel):
    answer: str
    sources: list[Source]
    question: str


class TopicInfo(BaseModel):
    id: str
    label: str
    description: str
    chunk_count: int
    active: bool


class TopicsResponse(BaseModel):
    topics: list[TopicInfo]


class HealthResponse(BaseModel):
    status: str
    chunks_loaded: int
    model: str


class FeedbackResponse(BaseModel):
    status: str


# ── Agent endpoint models (tax extension, Phase 3) ─────────────────────────────

class AgentQueryRequest(BaseModel):
    """Request body for POST /api/agent/query."""
    question: str = Field(..., min_length=1, max_length=2000,
                          description="Natural-language question about NZ employment law or tax law.")


class AgentSource(BaseModel):
    """A single cited source in an agent response."""
    title: str
    url: str
    content_type: str = "guide"   # guide | legislation | case | statistics


class AgentQueryResponse(BaseModel):
    """
    Response from POST /api/agent/query (v2 — risk-control fields added).

    Core answer fields:
        answer          — synthesised answer text (includes risk footer verbatim)
        sources         — cited sources (URL-allowlist validated)
        domains_used    — which tool(s) contributed: "employment", "tax", "labour_stats"
        tool_calls      — routing decisions (telemetry / debug; not shown in UI)
        refused         — True if the question was refused
        refusal_reason  — short reason string when refused=True
        chart           — ChartConfig dict from labour stats tool, or null
        question        — echo of the original question

    Risk-control metadata (v2):
        intent_class        — LOOKUP | ADVICE | HIGH_STAKES
        domain_tier         — H1 | H2 | H3 | M | L
        domain_label        — employment | tax | immigration | ...
        routing_outcome     — DIRECT_ANSWER | STRUCTURED_INFORMATIONAL |
                              STRUCTURED_ADVICE_SKELETON | REFUSE_WITH_REFERRAL
        crisis_route_fired  — True if crisis detector triggered
        regeneration_count  — how many times output_guard regenerated the answer
        risk_badge          — general_info | high_care | please_get_advice | refused
                              (drives the per-message badge colour in the UI)
    """
    # Core
    answer: str
    sources: list[AgentSource]
    domains_used: list[str]
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    refused: bool = False
    refusal_reason: str | None = None
    chart: dict[str, Any] | None = None
    question: str
    # Risk-control metadata
    intent_class: str = "LOOKUP"
    domain_tier: str = "M"
    domain_label: str = "other"
    routing_outcome: str = "DIRECT_ANSWER"
    crisis_route_fired: bool = False
    regeneration_count: int = 0
    risk_badge: str = "general_info"
    # Observability (Sprint 5)
    trace_id: str | None = None


# ── Consent / audit models (risk controls, v2) ────────────────────────────────

class ConsentAcknowledgeRequest(BaseModel):
    """
    Request body for POST /api/consent/acknowledge.

    The frontend sends this when the user clicks "I accept" or "I disagree"
    on the first-message disclaimer modal.

    NOTE: ip_hash is computed SERVER-SIDE from the raw request IP.
          Do NOT send ip_hash from the client — it will be ignored.
    """
    session_id: str = Field(..., min_length=1, max_length=128,
                            description="Frontend-generated session UUID.")
    event_type: Literal[
        "first_message_disclaimer",
        "disclaimer_declined",
        "reprompt",
        "policy_version_bump",
    ] = "first_message_disclaimer"
    user_id: str | None = None
    disclaimer_version: str = "1.0"
    privacy_policy_version: str = "1.0"
    checkbox_states: dict[str, bool] = Field(
        default_factory=dict,
        description='{"general_info": bool, "no_reliance": bool, "read_policies": bool}',
    )
    user_agent: str | None = Field(None, max_length=512)
    ui_locale: str = "en-NZ"
    # sha256(first_question_text) — optional, avoids storing sensitive text here
    question_hash: str | None = Field(None, max_length=64)


class ConsentAcknowledgeResponse(BaseModel):
    ok: bool
    event_id: str
