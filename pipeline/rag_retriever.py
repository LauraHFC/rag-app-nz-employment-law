"""
rag_retriever.py
================
Thin adapter around RAGSystem.retrieve().

Returns a RAGContext dataclass that the unified answer_generator can consume
without knowing anything about Chroma or the RAGSystem internals.

This module does NOT touch rag_query.py — it only calls retrieve().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pipeline.rag_query import RAGSystem

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RAGContext:
    """Result of a vector-store retrieval (no LLM generation yet)."""
    chunks: list[str]
    metadatas: list[dict]
    sources: list[dict] = field(default_factory=list)   # pre-formatted for HubQueryResponse


# ---------------------------------------------------------------------------
# Singleton accessor (mirrors intelligence_hub._get_rag pattern)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VS_DIR = _PROJECT_ROOT / "data" / "vectorstore"
_rag: Optional[RAGSystem] = None


def _get_rag(vectorstore_path: Path | None = None) -> RAGSystem:
    global _rag
    if _rag is None:
        _rag = RAGSystem(vectorstore_path or _VS_DIR)
    return _rag


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def retrieve(
    query: str,
    n_results: int = 5,
    vectorstore_path: Path | None = None,
) -> RAGContext:
    """
    Retrieve relevant legal chunks from the vector store.

    Returns a RAGContext with chunks, raw metadatas, and pre-formatted
    source dicts ready for HubQueryResponse.
    """
    rag = _get_rag(vectorstore_path)
    docs, metas = rag.retrieve(query, n_results=n_results)

    # Pre-format sources (same logic as RAGSystem.query)
    sources = [
        {
            "title": m.get("document_title", "Unknown"),
            "url": m.get("source_url", "Unknown"),
            "content_type": m.get("content_type", "guide"),
        }
        for m in metas
    ]

    log.info("Retrieved %d chunks for: %s", len(docs), query[:80])
    return RAGContext(chunks=docs, metadatas=metas, sources=sources)
