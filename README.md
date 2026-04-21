# NZ Employment Law Assistant

An AI-powered Q&A chatbot for New Zealand employment law — built with a full RAG pipeline, FastAPI backend, and React/Next.js frontend. Live at **[nzlaw.linkiwise.com](https://nzlaw.linkiwise.com)**.

> ⚖️ For informational purposes only — not legal advice. For serious matters, consult a qualified employment lawyer.

---

## Why This Exists

General AI tools can answer employment law questions — but they hallucinate, cite outdated legislation, or give confident-sounding answers with no way to verify them. This tool takes a different approach: every answer is grounded in retrieved content from authoritative NZ government sources, with the specific document and URL attached to every response. You can read the source yourself. You can verify it.

---

## Live Demo

**[https://nzlaw.linkiwise.com](https://nzlaw.linkiwise.com)** *(desktop recommended)*

Try asking:
- *What is the current minimum wage in New Zealand?*
- *How many days of annual leave am I entitled to?*
- *I've been made redundant after 5 years — what am I owed?*
- *What is a 90-day trial period and how does it work?*

---

## About This Project

This project was built as a complete end-to-end RAG application — from data pipeline design through to production deployment. The full development lifecycle covered: ideation, research, MVP scoping, PRD, implementation, testing, deployment, and validation.

The system was originally prototyped with a Streamlit UI. It has since been refactored to a production architecture: a FastAPI backend serving a REST API, and a React/Next.js frontend deployed independently — with a clean separation of concerns and extensibility for future knowledge bases.

<img width="1800" height="1240" alt="product-flow" src="https://github.com/user-attachments/assets/b3b351f9-22f7-4ba6-bbd2-b76fbc2db9f7" />

---

## Features

- ✅ Answers grounded in official NZ government legal documents
- ✅ Source citations with URLs for every response — fully verifiable
- ✅ Handles both simple factual questions and complex situational queries
- ✅ Declines to answer when retrieved context is insufficient (no hallucination)
- ✅ Production React/Next.js UI with topic-driven architecture
- ✅ Privacy-first — no user data collected, sessions are ephemeral

---

## System Architecture

```
Data Sources (NZ Government Websites)
         │
         ▼
Recursive Web Crawler
(1,283 URLs → 1,227 collected)
         │
         ▼
Data Cleaning & Extraction
(HTML + PDF → structured text)
         │
         ▼
Chunking & Quality Scoring       ← tiktoken + LangChain splitter
(1,960 chunks, 1,000 tokens avg)
         │
         ▼
Vectorisation & Indexing         ← pipeline/build_vectorstore.py
(ChromaDB + all-MiniLM-L6-v2)
         │
         ▼
RAG Query System                 ← pipeline/rag_query.py
(Semantic retrieval → Claude API → cited answer)
         │
         ▼
FastAPI REST API                 ← api/main.py (Railway)
(nzlaw-api.linkiwise.com)
         │
         ▼
React / Next.js Frontend         ← github.com/LauraHFC/nz-employment-law-frontend (Vercel)
(nzlaw.linkiwise.com)
```

---

## Data Pipeline Details

### Phase 1 — Data Collection

Automated collection from authoritative NZ government sources:

**Results:** 1,283 URLs discovered → 1,227 successfully collected (95.6%) → 1,233 HTML/PDF files

### Phase 2 — Cleaning & Chunking

- HTML parsed with BeautifulSoup4; PDFs extracted with pdfplumber (first 20 pages)
- Text normalised with ftfy; unicode cleaned; boilerplate removed
- Content typed as `legislation`, `case`, or `guide` for adaptive processing
- Token-aware chunking with LangChain `RecursiveCharacterTextSplitter` + tiktoken

| Content Type | Chunk Size | Overlap |
|---|---|---|
| Legislation | 1,200 tokens | 250 tokens |
| Case law | 1,100 tokens | 220 tokens |
| Guidance | 1,000 tokens | 200 tokens |

- MD5-based deduplication removed 202 redundant files
- Legal keyword quality scoring to identify high-value chunks

**Results:** 1,960 chunks generated, avg. 1,000 tokens each

### Phase 3 — Vectorisation

- **Embedding model:** `sentence-transformers/all-MiniLM-L6-v2` (local, no API required, 33M params)
- **Vector store:** ChromaDB with HNSW indexing and cosine similarity
- **1,960 chunks embedded in 17.8 seconds** (110 chunks/second)

### Phase 4 — Retrieval Validation

20-question test suite across 3 tiers before connecting the LLM:

| Tier | Type | Count | Coverage |
|------|------|-------|----------|
| 1 | Basic facts (minimum wage, annual leave, sick leave…) | 10 | 100% |
| 2 | Synthesis (redundancy calculation, multi-doc reasoning…) | 5 | 100% |
| 3 | Edge cases (contractor classification, foreign workers…) | 5 | 100% |

**Overall retrieval coverage: 100% (20/20)**

### Phase 5 — RAG Query System

```python
# Core flow
def query(question):
    docs, metas = retrieve(question, n_results=5)    # ChromaDB semantic search
    answer, sources = generate(question, docs, metas) # Claude API with source attribution
    return {"question": question, "answer": answer, "sources": sources}
```

System prompt instructs the model to: cite specific sections, be concise and practical, and explicitly decline when the answer is not in the retrieved documents.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11 |
| Web crawling | requests, BeautifulSoup4 |
| PDF extraction | pdfplumber |
| Text processing | ftfy, tiktoken |
| Chunking | LangChain RecursiveCharacterTextSplitter |
| Embeddings | sentence-transformers/all-MiniLM-L6-v2 |
| Vector database | ChromaDB |
| LLM | Anthropic Claude API (claude-haiku-4-5) |
| Backend API | FastAPI + uvicorn |
| Frontend | React / Next.js 14 (TypeScript) |
| Backend deployment | Railway |
| Frontend deployment | Vercel |

---

## Repositories

| Repo | Description |
|------|-------------|
| [rag-app-nz-employment-law](https://github.com/LauraHFC/rag-app-nz-employment-law) | Data pipeline + FastAPI backend |
| [nz-employment-law-frontend](https://github.com/LauraHFC/nz-employment-law-frontend) | React / Next.js frontend |

---

## Deployment

| Service | URL | Platform |
|---------|-----|----------|
| Frontend | [nzlaw.linkiwise.com](https://nzlaw.linkiwise.com) | Vercel |
| Backend API | [nzlaw-api.linkiwise.com](https://nzlaw-api.linkiwise.com) | Railway |

### Environment Variables (Backend)

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key (required) |

---

## Cost Estimate

Using Claude Haiku (most cost-effective model):

| Usage | Estimated Cost |
|-------|---------------|
| Per query | ~$0.015 |
| 100 queries/month | ~$1.50 |
| 1,000 queries/month | ~$15 |

---

## Privacy

No user data is collected. All conversations are temporary and exist only in your browser session — permanently deleted when you close or refresh the page.

---

**Author:** Laura Cai · [Portfolio](https://linkiwise.com) · [LinkedIn](https://www.linkedin.com/in/laurahfc/)

---

*For informational purposes only — not legal advice. All information sourced from official New Zealand government websites. For serious employment matters, consult a qualified employment lawyer.*
