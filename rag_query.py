"""
rag_query.py
------------
RAG (Retrieval-Augmented Generation) system for NZ Employment Law.

Retrieves relevant chunks from Chroma vector store and generates answers
using Claude API with source citations.

Usage:
    python3 pipeline/rag_query.py

Then type your questions interactively.
"""

import logging
import os
import sys
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
VS_DIR   = BASE_DIR / "data" / "vectorstore"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


class RAGSystem:
    def __init__(self, vectorstore_path: Path, api_key: str = None):
        """Initialize RAG system with Chroma + Claude."""
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not found. Set it via: "
                "export ANTHROPIC_API_KEY='your-key-here'"
            )

        # Load Chroma
        try:
            import chromadb
            self.client = chromadb.PersistentClient(path=str(vectorstore_path))
            self.collection = self.client.get_collection("nz_employment_law")
            log.info("Loaded Chroma collection: nz_employment_law")
        except ImportError:
            log.error("chromadb not installed")
            sys.exit(1)
        except Exception as e:
            log.error("Failed to load vector store: %s", e)
            sys.exit(1)

        # Load Anthropic client
        try:
            from anthropic import Anthropic
            self.anthropic = Anthropic(api_key=self.api_key)
            log.info("Loaded Anthropic client")
        except ImportError:
            log.error("anthropic not installed. Run: pip install anthropic --break-system-packages")
            sys.exit(1)

        self.system_prompt = """You are an expert in New Zealand employment law.
Answer questions based on the provided documents from the NZ government employment resources.

When answering:
1. Be concise and practical
2. Cite specific sections or documents when relevant
3. If the answer is not in the documents, say so clearly
4. Highlight key requirements and entitlements"""

    def retrieve(self, query: str, n_results: int = 5) -> tuple[list[str], list[dict]]:
        """Retrieve top-n relevant chunks from vector store."""
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
            )
            documents = results["documents"][0]
            metadatas = results["metadatas"][0]
            return documents, metadatas
        except Exception as e:
            log.error("Retrieval failed: %s", e)
            return [], []

    def generate(self, query: str, context_docs: list[str], context_meta: list[dict]) -> tuple[str, list[dict]]:
        """Generate answer using Claude with retrieved context."""
        # Build context string
        context = "\n\n---\n\n".join([
            f"[Source: {meta.get('source_url', 'Unknown')}]\n{doc}"
            for doc, meta in zip(context_docs, context_meta)
        ])

        user_message = f"""Based on the following NZ employment law documents, answer this question:

Question: {query}

Documents:
{context}

Provide a clear, practical answer with citations where appropriate."""

        try:
            response = self.anthropic.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=self.system_prompt,
                messages=[
                    {"role": "user", "content": user_message}
                ],
            )
            answer = response.content[0].text
            return answer, context_meta
        except Exception as e:
            log.error("Generation failed: %s", e)
            return f"Error generating answer: {e}", []

    def query(self, question: str, n_results: int = 5) -> dict:
        """Full RAG pipeline: retrieve → generate → cite sources."""
        log.info("Question: %s", question)

        # Retrieve
        docs, metas = self.retrieve(question, n_results=n_results)
        if not docs:
            return {
                "question": question,
                "answer": "No relevant documents found.",
                "sources": [],
            }

        log.info("Retrieved %d relevant chunks", len(docs))

        # Generate
        answer, source_metas = self.generate(question, docs, metas)

        # Format sources
        sources = []
        for meta in source_metas:
            sources.append({
                "url": meta.get("source_url", "Unknown"),
                "title": meta.get("document_title", "Unknown"),
                "content_type": meta.get("content_type", "Unknown"),
            })

        return {
            "question": question,
            "answer": answer,
            "sources": sources,
        }


def interactive_chat():
    """Run interactive Q&A session."""
    try:
        rag = RAGSystem(VS_DIR)
    except Exception as e:
        log.error("Failed to initialize RAG system: %s", e)
        sys.exit(1)

    log.info("=" * 70)
    log.info("NZ Employment Law RAG System")
    log.info("=" * 70)
    log.info("Type your question (or 'exit' to quit):\n")

    while True:
        try:
            question = input("Q: ").strip()
            if not question:
                continue
            if question.lower() in ("exit", "quit", "q"):
                print("Goodbye!")
                break

            result = rag.query(question)

            print("\n" + "=" * 70)
            print("ANSWER:")
            print("=" * 70)
            print(result["answer"])

            if result["sources"]:
                print("\n" + "-" * 70)
                print("SOURCES:")
                print("-" * 70)
                for i, src in enumerate(result["sources"], 1):
                    print(f"{i}. {src['title']}")
                    print(f"   URL: {src['url']}")
                    print(f"   Type: {src['content_type']}")
            print()

        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            log.error("Error: %s", e)


if __name__ == "__main__":
    interactive_chat()
