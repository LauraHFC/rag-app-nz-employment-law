"""
pipeline/tools/tenancy_search.py
=================================
Tool wrapper: search_tenancy_law

Wraps a tenancy-law RAG pipeline that mirrors the employment/tax ones, but reads
from the nz_tenancy_law ChromaDB collection (data/vectorstore_tenancy/).

The tenancy vector store is built separately in Sprint 7 Phase 1:
    python pipeline/recursive_crawler.py --tenancy
    python pipeline/extract_clean.py --source-dir data/raw_tenancy \
        --output data/chunks_tenancy/chunks.jsonl
    python pipeline/build_vectorstore.py --chunks data/chunks_tenancy/chunks.jsonl \
        --collection nz_tenancy_law --vectorstore-dir data/vectorstore_tenancy

If the vectorstore does not yet exist, execute() returns a graceful error
rather than crashing.

Public API:
    execute(inputs: dict) -> ToolResult
        inputs["query"] — retrieval query string
"""

from __future__ import annotations

import logging
from pathlib import Path

from pipeline.tools.employment_search import ToolResult  # reuse shared dataclass

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TENANCY_VS_DIR = _PROJECT_ROOT / "data" / "vectorstore_tenancy"

# Lazy singleton
_tenancy_client = None
_tenancy_collection = None
_tenancy_embed_fn = None


def _get_tenancy_collection():
    """Return the nz_tenancy_law ChromaDB collection, loading it once."""
    global _tenancy_client, _tenancy_collection, _tenancy_embed_fn
    if _tenancy_collection is None:
        if not _TENANCY_VS_DIR.exists():
            raise FileNotFoundError(
                f"Tenancy vector store not found at {_TENANCY_VS_DIR}. "
                "Run Sprint 7 Phase 1 data pipeline first:\n"
                "  python pipeline/recursive_crawler.py --tenancy\n"
                "  python pipeline/extract_clean.py --source-dir data/raw_tenancy "
                "--output data/chunks_tenancy/chunks.jsonl\n"
                "  python pipeline/build_vectorstore.py --chunks data/chunks_tenancy/chunks.jsonl "
                "--collection nz_tenancy_law --vectorstore-dir data/vectorstore_tenancy"
            )
        import chromadb
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        _tenancy_client = chromadb.PersistentClient(path=str(_TENANCY_VS_DIR))
        _tenancy_embed_fn = DefaultEmbeddingFunction()
        _tenancy_collection = _tenancy_client.get_collection(
            name="nz_tenancy_law",
            embedding_function=_tenancy_embed_fn,
        )
        log.info("Loaded tenancy ChromaDB collection: nz_tenancy_law (%d chunks)", _tenancy_collection.count())
    return _tenancy_collection


def execute(inputs: dict) -> ToolResult:
    """
    Execute the search_tenancy_law tool.

    Args:
        inputs: dict with key "query" (str) — retrieval query

    Returns:
        ToolResult with chunks and sources from the tenancy vector store
    """
    query = inputs.get("query", "").strip()
    if not query:
        return ToolResult(
            tool_name="search_tenancy_law",
            success=False,
            domain="tenancy",
            error="Empty query — no retrieval performed.",
        )

    try:
        collection = _get_tenancy_collection()
        results = collection.query(query_texts=[query], n_results=5)

        docs = results["documents"][0] if results["documents"] else []
        metas = results["metadatas"][0] if results["metadatas"] else []

        sources = [
            {
                "title": m.get("document_title", "Unknown"),
                "url": m.get("source_url", "Unknown"),
                "content_type": m.get("content_type", "guide"),
            }
            for m in metas
        ]

        log.info(
            "[search_tenancy_law] Retrieved %d chunks for: %s",
            len(docs),
            query[:80],
        )

        return ToolResult(
            tool_name="search_tenancy_law",
            success=True,
            chunks=docs,
            sources=sources,
            domain="tenancy",
            raw={"query": query, "n_results": len(docs)},
        )

    except FileNotFoundError as exc:
        log.error("[search_tenancy_law] Vector store missing: %s", exc)
        return ToolResult(
            tool_name="search_tenancy_law",
            success=False,
            domain="tenancy",
            error=str(exc),
        )
    except Exception as exc:
        log.exception("[search_tenancy_law] Retrieval error: %s", exc)
        return ToolResult(
            tool_name="search_tenancy_law",
            success=False,
            domain="tenancy",
            error=str(exc),
        )
