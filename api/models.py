# api/models.py — Pydantic request/response models (§3.2 handoff doc)

from pydantic import BaseModel, Field
from typing import Literal


# ── Request models ─────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    topic: str = Field(..., description="Knowledge base ID, e.g. 'nz_employment_law'")
    n_results: int = Field(default=5, ge=1, le=20)


class FeedbackRequest(BaseModel):
    question: str = Field(..., min_length=1)
    rating: Literal["up", "down"]
    topic: str = Field(..., description="Knowledge base ID")


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
