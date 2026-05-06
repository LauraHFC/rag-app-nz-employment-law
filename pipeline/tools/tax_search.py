"""
pipeline/tools/tax_search.py
==============================
Tool wrapper: search_tax_law

Wraps a tax-law RAG pipeline that mirrors the employment one, but reads from
the nz_tax_law ChromaDB collection (data/vectorstore_tax/).

The tax vector store is built separately in Phase 1 of the tax extension:
    python pipeline/build_vectorstore.py \
        --chunks data/chunks_tax/chunks.jsonl \
        --collection nz_tax_law \
        --vectorstore-dir data/vectorstore_tax

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
_TAX_VS_DIR = _PROJECT_ROOT / "data" / "vectorstore_tax"

# Lazy singleton — direct chromadb client (RAGSystem hardcodes "nz_employment_law",
# so we talk to chromadb directly for the tax collection)
_tax_client = None
_tax_collection = None
_tax_embed_fn = None


def _get_tax_collection():
    """Return the nz_tax_law ChromaDB collection, loading it once."""
    global _tax_client, _tax_collection, _tax_embed_fn
    if _tax_collection is None:
        if not _TAX_VS_DIR.exists():
            raise FileNotFoundError(
                f"Tax vector store not found at {_TAX_VS_DIR}. "
                "Run Phase 1 data pipeline first:\n"
                "  python pipeline/recursive_crawler.py --tax\n"
                "  python pipeline/extract_clean.py --source-dir data/raw_tax "
                "--output data/chunks_tax/chunks.jsonl\n"
                "  python pipeline/build_vectorstore.py --chunks data/chunks_tax/chunks.jsonl "
                "--collection nz_tax_law --vectorstore-dir data/vectorstore_tax"
            )
        import chromadb
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        _tax_client = chromadb.PersistentClient(path=str(_TAX_VS_DIR))
        _tax_embed_fn = DefaultEmbeddingFunction()
        _tax_collection = _tax_client.get_collection(
            name="nz_tax_law",
            embedding_function=_tax_embed_fn,
        )
        log.info("Loaded tax ChromaDB collection: nz_tax_law (%d chunks)", _tax_collection.count())
    return _tax_collection


def execute(inputs: dict) -> ToolResult:
    """
    Execute the search_tax_law tool.

    Args:
        inputs: dict with key "query" (str) — retrieval query

    Returns:
        ToolResult with chunks and sources from the tax vector store
    """
    query = inputs.get("query", "").strip()
    if not query:
        return ToolResult(
            tool_name="search_tax_law",
            success=False,
            domain="tax",
            error="Empty query — no retrieval performed.",
        )

    try:
        collection = _get_tax_collection()
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
            "[search_tax_law] Retrieved %d chunks for: %s",
            len(docs),
            query[:80],
        )

        return ToolResult(
            tool_name="search_tax_law",
            success=True,
            chunks=docs,
            sources=sources,
            domain="tax",
            raw={"query": query, "n_results": len(docs)},
        )

    except FileNotFoundError as exc:
        log.error("[search_tax_law] Vector store missing: %s", exc)
        return ToolResult(
            tool_name="search_tax_law",
            success=False,
            domain="tax",
            error=str(exc),
        )
    except Exception as exc:
        log.exception("[search_tax_law] Retrieval error: %s", exc)
        return ToolResult(
            tool_name="search_tax_law",
            success=False,
            domain="tax",
            error=str(exc),
        )
