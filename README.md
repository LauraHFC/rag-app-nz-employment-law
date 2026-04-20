# NZ Employment Law Assistant

An AI-powered Q&A chatbot for New Zealand employment law — built with a full RAG pipeline, Claude API, and Streamlit. Live at **[nzlaw.linkiwise.com](https://nzlaw.linkiwise.com)**.

> ⚖️ For informational purposes only — not legal advice. For serious matters, consult a qualified employment lawyer.

---

## Why This Exists

General AI tools can answer employment law questions — but they can hallucinate, cite outdated legislation, or give confident-sounding answers with no way to verify them. This tool takes a different approach: every answer is grounded in retrieved content from authoritative NZ government sources, with the specific document and URL attached to every response. You can read the source yourself. You can verify it.

---

## Live Demo

**[https://nzlaw.linkiwise.com](https://nzlaw.linkiwise.com)** *(desktop recommended)*

Try asking:
- *What is the current minimum wage in New Zealand?*
- *How many days of annual leave am I entitled to?*
- *I've been made redundant after 5 years — what am I owed?*
- *What is a 90-day trial period and how does it work?*

---

## Features

- ✅ Answers grounded in official NZ government legal documents
- ✅ Source citations with URLs for every response — fully verifiable
- ✅ Handles both simple factual questions and complex situational queries
- ✅ Declines to answer when retrieved context is insufficient (no hallucination)
- ✅ Clean Streamlit UI with example question chips
- ✅ Privacy-first — no user data collected, sessions are ephemeral

---

## System Architecture

```
Data Sources (6 NZ Government Websites)
         │
         ▼
Recursive Web Crawler          ← pipeline/recursive_crawler.py
(1,283 URLs → 1,227 collected)
         │
         ▼
Data Cleaning & Extraction     ← pipeline/extract_clean.py
(HTML + PDF → structured text)
         │
         ▼
Chunking & Quality Scoring     ← tiktoken + LangChain splitter
(1,960 chunks, 1,000 tokens avg)
         │
         ▼
Vectorisation & Indexing       ← pipeline/build_vectorstore.py
(ChromaDB + all-MiniLM-L6-v2)
         │
         ▼
RAG Query System               ← pipeline/rag_query.py
(Semantic retrieval → Claude API → cited answer)
         │
         ▼
Streamlit Web UI               ← app.py
```

---

## Data Pipeline Details

### Phase 1 — Data Collection

Automated collection from 6 authoritative NZ government sources:

| Source | Content |
|--------|---------|
| employment.govt.nz | Employment guidance |
| legislation.govt.nz | Official NZ legislation |
| era.govt.nz | Employment Relations Authority |
| dol.govt.nz | Department of Labour |
| mbie.govt.nz | Ministry of Business, Innovation & Employment |
| worksafe.govt.nz | WorkSafe New Zealand |

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

- Embedding model: `sentence-transformers/all-MiniLM-L6-v2` (local, no API required, 33M params)
- Vector store: ChromaDB with HNSW indexing and cosine similarity
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
    docs, metas = retrieve(question, n_results=5)   # ChromaDB semantic search
    answer, sources = generate(question, docs, metas) # Claude API with source attribution
    return {"question": question, "answer": answer, "sources": sources}
```

System prompt instructs the model to: cite specific sections, be concise and practical, and explicitly decline when the answer is not in the retrieved documents.

---

## Performance Metrics

| Metric | Value |
|--------|-------|
| Data sources | 6 NZ government websites |
| URLs collected | 1,227 (95.6% success rate) |
| Chunks in vector store | 1,960 |
| Retrieval coverage | 100% (20/20 test questions) |
| Embedding speed | 110 chunks/second |
| Vector DB build time | 17.8 seconds |
| Estimated API cost | ~$0.015 per query |
| Total Python code | ~2,000 lines |
| Total dev time | ~4 hours (with Vibe Coding) |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.10+ |
| Web crawling | requests, BeautifulSoup4 |
| PDF extraction | pdfplumber |
| Text processing | ftfy, tiktoken |
| Chunking | LangChain RecursiveCharacterTextSplitter |
| Embeddings | sentence-transformers/all-MiniLM-L6-v2 |
| Vector database | ChromaDB |
| LLM | Anthropic Claude API (claude-haiku-4-5) |
| Frontend | Streamlit |
| Deployment | Railway |

---

## Project Structure

```
.
├── app.py                       # Streamlit frontend application
├── pipeline/
│   ├── recursive_crawler.py     # Recursive web crawler (650+ lines)
│   ├── extract_clean.py         # Data extraction, cleaning & chunking (390+ lines)
│   ├── build_vectorstore.py     # Vector database construction (150+ lines)
│   ├── rag_query.py             # RAG query system — retrieval + generation (200+ lines)
│   ├── run_complete_crawl.py    # Pipeline orchestrator
│   └── collect.py               # Original collection reference
├── tests/
│   ├── run_retrieval_tests.py   # 20-question retrieval test suite (550+ lines)
│   ├── retrieval_test_queries.json  # Test questions + expected answers
│   └── reports/                 # Generated test reports (JSON + Markdown)
├── data/
│   ├── raw/                     # Crawled HTML/PDF files (not in repo)
│   ├── chunks/chunks.jsonl      # 1,960 processed chunks (not in repo)
│   └── vectorstore/             # ChromaDB database (not in repo)
├── docs/                        # Additional documentation
├── requirements.txt             # Python dependencies
├── Dockerfile                   # Container configuration
└── setup_mac.sh                 # Mac environment setup script
```

---

## Running Locally

### Prerequisites

- Python 3.10+
- An Anthropic API key — [get one here](https://console.anthropic.com)
- The pre-built vector store (see note below)

> **Note:** The `data/` directory (raw files, chunks, and vector store) is not included in this repository due to size. To rebuild from scratch, run the full pipeline as described below. This takes approximately 60–90 minutes for the crawl phase.

### Quick Start (with existing vector store)

```bash
# 1. Clone the repo
git clone https://github.com/LauraHFC/rag-app-nz-employment-law.git
cd rag-app-nz-employment-law

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set API key
export ANTHROPIC_API_KEY=sk-ant-...

# 5. Run
streamlit run app.py
```

App available at `http://localhost:8501`

### Rebuilding the Pipeline from Scratch

```bash
# Step 1: Crawl all sources (~60 min)
python pipeline/run_complete_crawl.py --all-sources

# Step 2: Extract, clean and chunk
python pipeline/extract_clean.py

# Step 3: Build vector store
python pipeline/build_vectorstore.py

# Step 4: Validate retrieval quality
python tests/run_retrieval_tests.py --output-format markdown

# Step 5: Run the app
streamlit run app.py
```

---

## Deployment

Deployed on **Railway** at [https://nzlaw.linkiwise.com](https://nzlaw.linkiwise.com).

### Environment Variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key (required) |
| `STREAMLIT_SERVER_PORT` | Server port (default: 8501) |

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

## About This Project

This project was built as a complete end-to-end RAG application — from data pipeline design through to production deployment. The full development lifecycle covered: ideation, research, MVP scoping, PRD, implementation, testing, deployment, and validation.

Built solo in approximately 4 hours of active development time, with AI-assisted development (Vibe Coding).

**Author:** Laura Cai · [Portfolio](https://linkiwise.com) · [LinkedIn](https://www.linkedin.com/in/laurahfc/)

---

*For informational purposes only — not legal advice. All information sourced from official New Zealand government websites. For serious employment matters, consult a qualified employment lawyer.*
