"""
pipeline/tools/consumer_search.py
==================================
Tool wrapper: search_consumer_law

Wraps a consumer-protection RAG pipeline that mirrors the employment/tax/tenancy
ones, reading from the nz_consumer_law ChromaDB collection (data/vectorstore_consumer/).

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
_CONSUMER_VS_DIR = _PROJECT_ROOT / "data" / "vectorstore_consumer"

# Lazy singleton
_consumer_client = None
_consumer_collection = None
_consumer_embed_fn = None


def _get_consumer_collection():
    """Return the nz_consumer_law ChromaDB collection, loading it once."""
    global _consumer_client, _consumer_collection, _consumer_embed_fn
    if _consumer_collection is None:
        if not _CONSUMER_VS_DIR.exists():
            raise FileNotFoundError(
                f"Consumer vector store not found at {_CONSUMER_VS_DIR}. "
                "Run the consumer data pipeline first:\n"
                "  python pipeline/batch_crawl_consumer.py --source <source>\n"
                "  python pipeline/extract_clean.py --source-dir data/raw_consumer "
                "--output data/chunks_consumer/chunks.jsonl\n"
                "  python pipeline/build_vectorstore.py --chunks data/chunks_consumer/chunks.jsonl "
                "--collection nz_consumer_law --vectorstore-dir data/vectorstore_consumer"
            )
        import chromadb
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        _consumer_client = chromadb.PersistentClient(path=str(_CONSUMER_VS_DIR))
        _consumer_embed_fn = DefaultEmbeddingFunction()
        _consumer_collection = _consumer_client.get_collection(
            name="nz_consumer_law",
            embedding_function=_consumer_embed_fn,
        )
        log.info("Loaded consumer ChromaDB collection: nz_consumer_law (%d chunks)", _consumer_collection.count())
    return _consumer_collection


def execute(inputs: dict) -> ToolResult:
    """
    Execute the search_consumer_law tool.

    Args:
        inputs: dict with key "query" (str) — retrieval query

    Returns:
        ToolResult with chunks and sources from the consumer vector store
    """
    query = inputs.get("query", "").strip()
    if not query:
        return ToolResult(
            tool_name="search_consumer_law",
            success=False,
            domain="consumer",
            error="Empty query — no retrieval performed.",
        )

    try:
        collection = _get_consumer_collection()
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
            "[search_consumer_law] Retrieved %d chunks for: %s",
            len(docs),
            query[:80],
        )

        return ToolResult(
            tool_name="search_consumer_law",
            success=True,
            chunks=docs,
            sources=sources,
            domain="consumer",
            raw={"query": query, "n_results": len(docs)},
        )

    except FileNotFoundError as exc:
        log.error("[search_consumer_law] Vector store missing: %s", exc)
        return ToolResult(
            tool_name="search_consumer_law",
            success=False,
            domain="consumer",
            error=str(exc),
        )
    except Exception as exc:
        log.exception("[search_consumer_law] Retrieval error: %s", exc)
        return ToolResult(
            tool_name="search_consumer_law",
            success=False,
            domain="consumer",
            error=str(exc),
        )
